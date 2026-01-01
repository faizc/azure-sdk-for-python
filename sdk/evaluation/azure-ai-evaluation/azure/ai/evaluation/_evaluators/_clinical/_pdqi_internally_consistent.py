import os
import json
from typing import Dict, Union
from azure.ai.evaluation._evaluators._common import PromptyEvaluatorBase
from typing_extensions import overload, override
import math
import logging

logger = logging.getLogger(__name__)

class PDQIInternallyConsistentEvaluator(PromptyEvaluatorBase):

    # Constants must be defined within eval's directory to be save/loadable
    _PROMPTY_FILE = "pdqi_internally_consistent.prompty"
    _RESULT_KEY = "PDQIInternallyConsistentEvaluator"
    @override
    def __init__(self, model_config, *, credential=None, threshold=3, **kwargs):
        current_dir = os.path.dirname(__file__)
        prompty_path = os.path.join(current_dir, self._PROMPTY_FILE)
        super().__init__(
            model_config=model_config,
            prompty_file=prompty_path,
            result_key=self._RESULT_KEY,
            threshold=threshold,
            credential=credential,
            _higher_is_better=True,
            **kwargs,
        )

    @overload
    def __call__(
        self,
        *,
        note: str,
    ) -> Dict[str, Union[str, float]]:
        """Evaluate groundedness for given input of query, response, context

        :keyword note: The note to be evaluated.
        :paramtype note: str
        :return: The relevance score.
        :rtype: Dict[str, float]
        """

    @override
    def __call__(  # pylint: disable=docstring-missing-param
        self,
        *args,
        **kwargs,
    ):
        """Evaluate relevance. Accepts either a query and response for a single evaluation,
        or a conversation for a multi-turn evaluation. If the conversation has more than one turn,
        the evaluator will aggregate the results of each turn.

        :keyword query: The query to be evaluated. Mutually exclusive with the `conversation` parameter.
        :paramtype query: Optional[str]
        :keyword response: The response to be evaluated. Mutually exclusive with the `conversation` parameter.
        :paramtype response: Optional[str]
        :keyword conversation: The conversation to evaluate. Expected to contain a list of conversation turns under the
            key "messages", and potentially a global context under the key "context". Conversation turns are expected
            to be dictionaries with keys "content", "role", and possibly "context".
        :paramtype conversation: Optional[~azure.ai.evaluation.Conversation]
        :return: The relevance score.
        :rtype: Union[Dict[str, Union[str, float]], Dict[str, Union[float, Dict[str, List[Union[str, float]]]]]]
        """
        return super().__call__(*args, **kwargs)    
    
    async def _do_eval(self, eval_input: Dict) -> Dict[str, Union[float, str]]:  # type: ignore[override]
        """Do a relevance evaluation.

        :param eval_input: The input to the evaluator. Expected to contain
        whatever inputs are needed for the _flow method, including context
        and other fields depending on the child class.
        :type eval_input: Dict
        :return: The evaluation result.
        :rtype: Dict
        """
        result = await self._flow(timeout=self._LLM_CALL_TIMEOUT, **eval_input)
        llm_output = json.loads(result.get("llm_output"))
        score = math.nan
        
        if isinstance(llm_output, dict):
            score = float(llm_output.get("score", math.nan))
            reason = llm_output.get("reason", "")
            # Parse out score and reason from evaluators known to possess them.
            binary_result = self._get_binary_result(score)
            return {
                self._result_key: float(score),
                f"{self._result_key}_result": binary_result,
                f"{self._result_key}_reason": reason,
                f"{self._result_key}_prompt_tokens": result.get("input_token_count", 0),
                f"{self._result_key}_completion_tokens": result.get("output_token_count", 0),
                f"{self._result_key}_total_tokens": result.get("total_token_count", 0),
                f"{self._result_key}_finish_reason": result.get("finish_reason", ""),
                f"{self._result_key}_model": result.get("model_id", ""),
                f"{self._result_key}_sample_input": result.get("sample_input", ""),
                f"{self._result_key}_sample_output": result.get("sample_output", ""),
            }

        if logger:
            logger.warning("LLM output is not a dictionary, returning NaN for the score.")

        binary_result = self._get_binary_result(score)
        return {
            self._result_key: float(score),
            f"{self._result_key}_result": binary_result,
        }            