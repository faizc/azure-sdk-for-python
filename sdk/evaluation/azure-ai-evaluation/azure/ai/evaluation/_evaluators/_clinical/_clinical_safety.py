import os
import json
from typing import Dict, Union
import typing
from azure.ai.evaluation._evaluators._common import PromptyEvaluatorBase
from typing_extensions import overload, override
import math
import logging
import typing
from collections import defaultdict
from azure.ai.textanalytics import TextAnalysisClient
from azure.ai.textanalytics.models import (
    MultiLanguageTextInput,
    MultiLanguageInput,
    AnalyzeTextOperationAction,
    HealthcareLROTask,
    HealthcareLROResult,
    RelationType
)
from azure.core.credentials import AzureKeyCredential
from dataclasses import dataclass, asdict
from drugapp import DrugBankAPI, DrugProviderRegistry, MedicationInNote, DrugIntolerance, Medication, MatchStatus

logger = logging.getLogger(__name__)

class ClinicalSafetyEvaluator(PromptyEvaluatorBase):

    # Constants must be defined within eval's directory to be save/loadable
    _PROMPTY_FILE = "clinical_safety.prompty"
    _RESULT_KEY = "ClinicalSafetyEvaluator"

    diagnosis: typing.Dict[str, str] = defaultdict(str)
    conditions: typing.Dict[str, str] = defaultdict(str)
    medication_to_dosage: typing.Dict[str, str] = defaultdict(str)
    medication_to_route: typing.Dict[str, str] = defaultdict(str)
    patientinfo_dict = {}
    drugBankAPI:DrugBankAPI = DrugProviderRegistry.get("drugbank")
    text_analytics_endpoint = None  
    text_analytics_credentials = None

    @override
    def __init__(self, model_config, *, credential=None, threshold=3, **kwargs):
        current_dir = os.path.dirname(__file__)
        prompty_path = os.path.join(current_dir, self._PROMPTY_FILE)
        self.text_analytics_endpoint = model_config['text_analytics_endpoint']
        self.text_analytics_credentials = AzureKeyCredential(model_config['text_analytics_key'])
        model_config.pop('text_analytics_endpoint', None)
        model_config.pop('text_analytics_key', None)
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
        """Evaluate clinical safety for given input note.

        :keyword note: The note to be evaluated.
        :paramtype note: str
        :return: The clinical safety score.
        :rtype: Dict[str, float]
        """

    @override
    def __call__(  # pylint: disable=docstring-missing-param
        self,
        *args,
        **kwargs,
    ):
        """Evaluate clinical safety. Accepts either a query and response for a single evaluation,
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
        :return: The clinical safety score.
        :rtype: Union[Dict[str, Union[str, float]], Dict[str, Union[float, Dict[str, List[Union[str, float]]]]]]
        """
        return super().__call__(*args, **kwargs)   

    def extract_healthcare_entities(self, note: str):
        entities = {}
        count = -1
        # get settings
        client = TextAnalysisClient(self.text_analytics_endpoint, credential=self.text_analytics_credentials)

        text_input = MultiLanguageTextInput(
            multi_language_inputs=[
                MultiLanguageInput(id="A", text=note, language="en"),
            ]
        )

        actions: list[AnalyzeTextOperationAction] = [
            HealthcareLROTask(
                name="Healthcare Operation",
            ),
        ]

        # Start long-running operation (sync) – poller returns ItemPaged[TextActions]
        poller = client.begin_analyze_text_job(
            text_input=text_input,
            actions=actions,
        )

        # Operation metadata (pre-final)
        print(f"Operation ID: {poller.details.get('operation_id')}")

        # Wait for completion and get pageable of TextActions
        paged_actions = poller.result()

        # Final-state metadata
        d = poller.details

        # Iterate results (sync pageable)
        for actions_page in paged_actions:
            for op_result in actions_page.items_property or []:
                if isinstance(op_result, HealthcareLROResult):
                    hc_result = op_result.results

                    for doc in (hc_result.documents or []):
                        for entity in (doc.entities or []):
                            count += 1
                            entities[count] = entity._data
                            #print(entity.category)
                            if entity.category == "Diagnosis":
                                #print(f"entity._data: {entity._data}")
                                if (
                                    "assertion" in entity._data
                                    and "temporality" in entity._data["assertion"]
                                ):
                                    self.diagnosis[entity.text] = entity._data['assertion']['temporality']
                                else:
                                    self.diagnosis[entity.text] = "N/A"
                            elif entity.category == "SymptomOrSign":
                                if (
                                    "assertion" in entity._data
                                    and "certainty" in entity._data["assertion"]
                                ):
                                    self.conditions[entity.text] = entity._data['assertion']['certainty']
                                else:
                                    self.conditions[entity.text] = "N/A"
                            elif entity.category == "Age":
                                #print(f"entity._data: {entity._data}")
                                self.patientinfo_dict['age'] = entity.text
                            elif entity.category == "Gender":
                                #print(f"entity._data: {entity._data}")  
                                self.patientinfo_dict['gender'] = entity.text

                        for relation in (doc.relations or []):
                            if relation.relation_type == RelationType.DOSAGE_OF_MEDICATION and len(relation.entities) == 2:
                                if entities[int(relation.entities[0].ref.split("/")[-1])]['category'] == 'MedicationName' and entities[int(relation.entities[1].ref.split("/")[-1])]['category'] == 'Dosage':
                                    self.medication_to_dosage[entities[int(relation.entities[0].ref.split("/")[-1])]['text']] = entities[int(relation.entities[1].ref.split("/")[-1])]['text']
                            elif relation.relation_type == RelationType.ROUTE_OF_MEDICATION and len(relation.entities) == 2:
                                self.medication_to_route[entities[int(relation.entities[0].ref.split("/")[-1])]['text']] = entities[int(relation.entities[1].ref.split("/")[-1])]['text']

                        print(f"Medication to Dosage mapping:{self.medication_to_dosage}")
                        print(f"Medication to Route mapping:{self.medication_to_route}")
                        print(f"Diagnosis mapping:{self.diagnosis}")
                        print(f"Conditions mapping:{self.conditions}")
                    # Other action kinds, if present
                    try:
                        print(
                            f"\n[Non-healthcare action] name={op_result.task_name}, "
                            f"status={op_result.status}, kind={op_result.kind}"
                        )
                    except Exception:
                        print("\n[Non-healthcare action present]") 
  
    async def _do_eval(self, eval_input: Dict) -> Dict[str, Union[float, str]]:  # type: ignore[override]
        """Do a clinical safety evaluation.

        :param eval_input: The input to the evaluator. Expected to contain
        whatever inputs are needed for the _flow method, including context
        and other fields depending on the child class.
        :type eval_input: Dict
        :return: The evaluation result.
        :rtype: Dict
        """
        
        RED = "\033[31m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        RESET = "\033[0m"

        notes_drug_intolerance_list: list[DrugIntolerance] = []
        notes_medication_list: list[MedicationInNote] = []
        notes_medication_not_found_list: list[MedicationInNote] = []

        self.extract_healthcare_entities(eval_input['note'])

        for medication_name, dosage_in_text in self.medication_to_dosage.items():
            medication_key = medication_name.lower()
            if medication_key in self.drugBankAPI.medication_lookup:
                drugbank_id = self.drugBankAPI.medication_lookup[medication_key]
                if drugbank_id in self.drugBankAPI.drug_info_lookup:
                    notes_medication_list.append(MedicationInNote(drugbank_id=drugbank_id, medication=medication_name, dosage=dosage_in_text))
                else:
                    notes_medication_not_found_list.append(MedicationInNote(drugbank_id='NA', medication=medication_name, dosage=dosage_in_text))
                    print(f"DrugBank ID {drugbank_id} not found in lookup.")
            else:
                notes_medication_not_found_list.append(MedicationInNote(drugbank_id='NA', medication=medication_name, dosage=dosage_in_text))
                print(f"Medication '{medication_name}' not found in medication lookup.")

        for medication in notes_medication_list:
            print(f"Medication Found - DrugBank ID: {medication.drugbank_id}, Name: {medication.medication}, Dosage: {medication.dosage}")
        for medication in notes_medication_not_found_list:
            print(f"Medication Not Found - Name: {medication.medication}, Dosage: {medication.dosage}")

        for medication in notes_medication_list:
            print(f"Medication Found - DrugBank ID: {medication.drugbank_id}, Name: {medication.medication}, Dosage: {medication.dosage}")
            medication1, status1 = self.drugBankAPI.validate_dosage(drugName=medication.medication, drugDosage=medication.dosage, age=None, body_weight=None)
            print(f"{YELLOW}Dosage validation for {medication.medication}: Status - {status1}, Medication Info - {medication1}{RESET}")
            medication2 = None
            status2 = MatchStatus.NONE
            if status1 != MatchStatus.NONE:
                medication2, status2 = self.drugBankAPI.validate_route(medication=medication1, expected_route=self.medication_to_route.get(medication.medication, ""))
            print(f"{YELLOW}Route validation for {medication.medication}: Status - {status2}, Medication Info - {medication2}{RESET}")
            #print(f"DrugBank Medication Dosage Info {drugbank_medication_list[medication.medication.lower()]}")
            #print(f"check_dosage_match result: {check_dosage_match(medication.dosage, drugbank_medication_list[medication.medication.lower()])}")
            #print(f"medication_to_route {medication_to_route[medication.medication.lower()]}")
            #print(medication_to_route)

        for i in range(len(notes_medication_list)):
            for j in range(i + 1, len(notes_medication_list)):
                print(notes_medication_list[i], notes_medication_list[j])
                drug_info = self.drugBankAPI.drug_info_lookup[notes_medication_list[i].drugbank_id]
                if notes_medication_list[i].drugbank_id in self.drugBankAPI.drug_intolerance_lookup[notes_medication_list[j].drugbank_id]:   
                    print(f"  Found drug intolerance between {notes_medication_list[i].medication} and {notes_medication_list[j].medication}")
                    print(f"  Drug Intolerance Info: {self.drugBankAPI.drug_intolerance_description[f'{notes_medication_list[i].drugbank_id}_{notes_medication_list[j].drugbank_id}']}")
                    notes_drug_intolerance_list.append(DrugIntolerance(
                        drugbank_id=notes_medication_list[i].drugbank_id,
                        drug_name=notes_medication_list[i].medication,
                        intolerance_drugbank_id=notes_medication_list[j].drugbank_id,
                        intolerance_drug_name=notes_medication_list[j].medication,
                        description=self.drugBankAPI.drug_intolerance_description[f"{notes_medication_list[i].drugbank_id}_{notes_medication_list[j].drugbank_id}"]
                    ))

        print(f"notes_drug_intolerance_list:{notes_drug_intolerance_list}")
        print(f"Diagnosis mapping:{self.diagnosis}")
        print(f"Conditions mapping:{self.conditions}")   
        print(f'self.patientinfo_dict : {self.patientinfo_dict}')

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
                f"{self._result_key}_intolerance_list": notes_drug_intolerance_list,
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