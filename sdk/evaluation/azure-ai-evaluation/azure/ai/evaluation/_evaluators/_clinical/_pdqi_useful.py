import os
import json
import sys
from promptflow.client import load_flow
from typing import Dict, Union

class PDQIUsefulEvaluator:
    def __init__(self, model_config):
        print(f"PDQIUsefulEvaluator __init__ {type(self)}")
        current_dir = os.path.dirname(__file__)
        prompty_path = os.path.join(current_dir, "pdqi_useful.prompty")
        self._flow = load_flow(source=prompty_path, model={"configuration": model_config})

    def __call__(self, *, response: str, **kwargs):
        print(f"PDQIUsefulEvaluator __call__ {type(self)}")
        llm_response = self._flow(response=response)
        try:
            response = json.loads(llm_response)
        except Exception as ex:
            response = llm_response
        return response
    
    async def _do_eval(self, eval_input: Dict) -> Dict[str, Union[float, str]]:  # type: ignore[override]
        """Do a relevance evaluation.

        :param eval_input: The input to the evaluator. Expected to contain
        whatever inputs are needed for the _flow method, including context
        and other fields depending on the child class.
        :type eval_input: Dict
        :return: The evaluation result.
        :rtype: Dict
        """
        print(f"PDQIUsefulEvaluator called {type(self)} with eval_input {eval_input}")
        return {
            "score": 1.43,
        }