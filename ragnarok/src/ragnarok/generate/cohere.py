import time
from typing import Any, Dict, List, Tuple, Union

import cohere
from ftfy import fix_text

from ragnarok.data import RAGExecInfo, Request
from ragnarok.generate.api_keys import get_cohere_api_key
from ragnarok.generate.llm import LLM, PromptMode
from ragnarok.generate.post_processor import CoherePostProcessor


class Cohere(LLM):
    def __init__(
        self,
        model: str,
        context_size: int,
        prompt_mode: PromptMode = PromptMode.COHERE,
        max_output_tokens: int = 1500,
        num_few_shot_examples: int = 0,
        key: str = get_cohere_api_key(),
    ) -> None:
        """
        Creates instance of the Cohere class, to deal with Cohere Command R models.

        Parameters:
        - model (str): The model identifier for the LLM (model identifier information can be found via OpenAI's model lists).
        - context_size (int): The maximum number of tokens that the model can handle in a single request.
        - prompt_mode (PromptMode, optional): Specifies the mode of prompt generation, with the default set to RANK_GPT,
         indicating that this class is designed primarily for listwise ranking tasks following the RANK_GPT methodology.
        - max_output_tokens (int, optional): Maximum number of tokens that can be generated in a single response. Defaults to 1500.
        - num_few_shot_examples (int, optional): Number of few-shot learning examples to include in the prompt, allowing for
        the integration of example-based learning to improve model performance. Defaults to 0, indicating no few-shot examples
        by default.
        - key (str, optional): The Cohere API key, defaults to the value of the COHERE_API_KEY environment variable.

        Raises:
        - ValueError: If an unsupported prompt mode is provided or if no Cohere API key / invalid key is supplied.
        """
        if model not in ["command-r-plus-08-2024", "command-r-plus", "command-r"]:
            raise ValueError(
                f"Unsupported model: {model}. The only models currently supported are 'command-r' and 'command-r-plus' in Cohere."
            )
        super().__init__(
            model, context_size, prompt_mode, max_output_tokens, num_few_shot_examples
        )
        self._client = cohere.Client(key)
        self._post_processor = CoherePostProcessor()
        # self._preamble = (
        #     "## Task And Context\n"
        #     + "You help people answer their questions. "
        #     + "You will be asked a very wide array of question on all kinds of topics. "
        #     + "You should focus on serving the user's needs as best you can, which will be wide-ranging\n\n"
        # )
        # self._preamble += (
        #     "## Style Guide\n"
        #     + "Answer in full sentences, using proper grammar and spelling. "
        #     + "Provide sentence-level citations, ensuring each sentence cites at most three sources. "
        #     + "Order the citations in decreasing order of importance. "
        #     + "Do not be chatty, just answer the question directly. "
        #     + "Ensure the answer is between 300 and 400 words long, comprehensive, well-cited, and detailed."
        # )
        self._preamble = (
            "## Task And Context\n"
            "You assist healthcare professionals in answering biomedical questions. "
            "These questions may cover a wide range of topics including diseases, treatments, medications, and patient care. "
            "Your primary focus is to provide accurate, relevant, and well-supported information to aid in clinical decision-making or patient education.\n\n"
        )
        self._preamble += (
            "## Style Guide\n"
            "Answer in full sentences, using clear and interpretable language. "
            "Provide sentence-level citations, ensuring each sentence cites at most three sources. "
            "Order the citations in decreasing order of importance. "
            "Focus solely on answering the question directly without any meta-commentary. "
            "Ensure the answer is concise (maximum 150 words excluding references), information-dense, and well-cited. "
            "Prioritize required and relevant information, avoiding unnecessary or borderline content. "
            "For patient-oriented questions, provide information suitable for clinician review and subsequent explanation. "
            "Express uncertainty when appropriate and acknowledge any contradictions in the sources. "
            "Avoid potentially harmful advice or unverified claims. "
        )

    def run_llm(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        logging: bool = False,
    ) -> Tuple[Any, RAGExecInfo]:
        query, top_k_docs = prompt[0]["query"], prompt[0]["context"]
        if logging:
            print(f"Query: {query}")
            print(f"Top K Docs: {top_k_docs}")
        while True:
            try:
                response = self._client.chat(
                    model=self._model,
                    preamble=self._preamble,
                    message=query,
                    documents=top_k_docs,
                )
                break
            except Exception as e:
                print(str(e))
                if "blocked output" in str(e):
                    answers = []
                    rag_exec_info = RAGExecInfo(
                        prompt=prompt[0],
                        response="Blocked output",
                        input_token_count=len(query.split())
                        + sum([len(doc["snippet"].split()) for doc in top_k_docs]),
                        output_token_count=0,
                        candidates=top_k_docs,
                    )
                    return answers, rag_exec_info
                time.sleep(60)
        answers, rag_exec_response = self._post_processor(response)
        if logging:
            print(f"Answers: {answers}")
        rag_exec_info = RAGExecInfo(
            prompt=prompt[0],
            response=rag_exec_response,
            input_token_count=len(query.split())
            + sum([len(doc["snippet"].split()) for doc in top_k_docs]),
            output_token_count=sum([len(ans.text) for ans in answers]),
            candidates=top_k_docs,
        )

        return answers, rag_exec_info

    def create_prompt(
        self, request: Request, topk: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        query = request.query.text
        max_length = (self._context_size - 200) // topk
        self._prompt_mode = PromptMode.COHERE
        while True:
            rank = 0
            context = []
            for cand in request.candidates[:topk]:
                rank += 1
                content = self.convert_doc_to_prompt_content(cand.doc, max_length)
                context.append(content)
            if self._prompt_mode == PromptMode.COHERE:
                messages = [{"query": query, "context": context}]
            num_tokens = self.get_num_tokens(messages)
            if num_tokens <= self.max_tokens() - self.num_output_tokens():
                break
            else:
                max_length -= max(
                    1,
                    (num_tokens - self.max_tokens() + self.num_output_tokens())
                    // (topk * 4),
                )
        return messages, self.get_num_tokens(messages)

    def get_num_tokens(self, prompt: Union[str, List[Dict[str, str]]]) -> int:
        """Returns the number of tokens used by a list of messages in prompt."""
        # TODO(ronak): Add support
        return -1

    def cost_per_1k_token(self, input_token: bool) -> float:
        # TODO(ronak): Add support
        return -1

    def convert_doc_to_prompt_content(
        self, doc: Dict[str, Any], max_length: int
    ) -> str:
        if "text" in doc:
            content = doc["text"]
        elif "segment" in doc:
            content = doc["segment"]
        elif "contents" in doc:
            content = doc["contents"]
        else:
            content = doc["passage"]
        content = {"snippet": content}
        if "title" in doc:
            content["title"] = doc["title"]
        for key in content:
            content[key] = content[key].strip()
            content[key] = fix_text(content[key])
            content[key] = content[key].replace("\n", " ")
            content[key] = " ".join(content[key].split()[: int(max_length)])
            content[key] = self._replace_number(content[key])
        return content
