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

logger = logging.getLogger(__name__)


class ClinicalUtils:
    drug_intolerance_lookup = {}
    drug_intolerance_description = {}
    drug_info_lookup = {}
    medication_lookup = {}

    def __init__(self):
        self.load_drugs
        self.load_drug_intolerance_info()

    def load_drug_intolerance_info(self):
        print("Loading drug intolerance information...")
        with open('drug-intolerance.json', 'r') as file:
            # Deserialize the JSON data into a Python dictionary
            data = json.load(file)
            #print(len(data))
            for x in range(len(data)):
                python_object = json.loads(data[x])
                #print(f"drugbank_id: {python_object['drugbank_id']} - {python_object['intolerance_drugbank_id']}\n")
                descKey = (f"{python_object['drugbank_id']}_{python_object['intolerance_drugbank_id']}")

                if python_object['drugbank_id'] not in self.drug_intolerance_lookup:
                    self.drug_intolerance_lookup[python_object['drugbank_id']] = []
                if descKey not in self.drug_intolerance_description:
                    self.drug_intolerance_description[descKey] = []

                #if(python_object['drugbank_id'] == "DB00722" and python_object['intolerance_drugbank_id'] == "DB00695"):
                #    print(f"Found entry: {python_object} with the desckey {descKey} \n")

                self.drug_intolerance_lookup[python_object['drugbank_id']].append(python_object['intolerance_drugbank_id'])
                self.drug_intolerance_description[descKey].append(python_object['description'])

    def load_drugs(self):
        print("Loading drug information...")
        try:
            with open('mydatallm.json', 'r') as file:
                # Deserialize the JSON data into a Python dictionary
                data = json.load(file)
                print(f'Total records in mydatallm.json: {len(data)}')
                for x in range(len(data)):
                    self.drug_info_lookup[data[x]["drugbank_id"]] = data[x]
                    for y in range(len(data[x]["medication"])):        
                        self.medication_lookup[data[x]['medication'][y]['drugName'].lower()] = data[x]["drugbank_id"]
        except FileNotFoundError:
            print("Error: The file 'mydatallm.json' was not found.")
        except json.JSONDecodeError:
            print("Error: Failed to decode JSON from the file.")    

clinical_utils = ClinicalUtils()  # singleton instance

@dataclass(frozen=True)
class MedicationInNote:
    drugbank_id: str
    medication: str
    dosage: str 

@dataclass(frozen=True)
class DrugIntolerance:
    drugbank_id: str
    drug_name: str
    intolerance_drugbank_id: str
    intolerance_drug_name: str
    description: str

@dataclass(frozen=True)
class Medication():
    drugName: str
    Dosage: str
    Strength: str
    Route: str

class ClinicalSafetyEvaluator(PromptyEvaluatorBase):

    # Constants must be defined within eval's directory to be save/loadable
    _PROMPTY_FILE = "clinical_safety.prompty"
    _RESULT_KEY = "ClinicalSafetyEvaluator"

    diagnosis: typing.Dict[str, str] = defaultdict(str)
    conditions: typing.Dict[str, str] = defaultdict(str)
    medication_to_dosage: typing.Dict[str, str] = defaultdict(str)
    condition_to_body_site: typing.Dict[str, str] = defaultdict(str)
    patientinfo_dict = {}

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
        endpoint = 'https://langeusfc001.cognitiveservices.azure.com/'
        credential = AzureKeyCredential('GBhQCKHWsbhYjWpuIfMwwYgxIxGakTXiQx64FVYxZ7LmyacNLenIJQQJ99BLACYeBjFXJ3w3AAAaACOGYDmb')

        client = TextAnalysisClient(endpoint, credential=credential)

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
        print(f"Job ID: {d.get('job_id')}")
        print(f"Status: {d.get('status')}")
        print(f"Created: {d.get('created_date_time')}")
        print(f"Last Updated: {d.get('last_updated_date_time')}")
        if d.get("expiration_date_time"):
            print(f"Expires: {d.get('expiration_date_time')}")
        if d.get("display_name"):
            print(f"Display Name: {d.get('display_name')}")

        # Iterate results (sync pageable)
        for actions_page in paged_actions:
            print(
                f"Completed: {actions_page.completed}, "
                f"In Progress: {actions_page.in_progress}, "
                f"Failed: {actions_page.failed}, "
                f"Total: {actions_page.total}"
            )

            for op_result in actions_page.items_property or []:
                if isinstance(op_result, HealthcareLROResult):
                    print(f"\nAction Name: {op_result.task_name}")
                    print(f"Action Status: {op_result.status}")
                    print(f"Kind: {op_result.kind}")

                    hc_result = op_result.results

                    for doc in (hc_result.documents or []):
                        print(f"\nDocument ID: {doc.id}")
                        
                        # Entities
                        print(f"Entities: {doc.entities}")
                        for entity in (doc.entities or []):
                            count += 1
                            print(f"  Category: {entity.category}")
                            entities[count] = entity._data
                            if entity.category == "Diagnosis":
                                if 'assertion' in entity._data:
                                    self.diagnosis[entity.text] = entity._data['assertion']['temporality']
                                else:
                                    self.diagnosis[entity.text] = "N/A"
                            elif entity.category == "SymptomOrSign":
                                if 'assertion' in entity._data:
                                    self.conditions[entity.text] = entity._data['assertion']['certainty']
                                else:
                                    self.conditions[entity.text] = "N/A"
                            elif entity.category == "Age":
                                self.patientinfo_dict['age'] = entity.text
                                print(f"Extracted Age: {entity.text}")
                            elif entity.category == "Gender":
                                self.patientinfo_dict['gender'] = entity.text
                                print(f"Extracted Gender: {entity.text}")

                        for relation in (doc.relations or []):
                            if relation.relation_type == RelationType.DOSAGE_OF_MEDICATION and len(relation.entities) == 2:
                                self.medication_to_dosage[entities[int(relation.entities[0].ref.split("/")[-1])]['text']] = entities[int(relation.entities[1].ref.split("/")[-1])]['text']

                        print(f"Medication to Dosage mapping:{self.medication_to_dosage}")
                        print(f"Condition to Body Site mapping:{self.condition_to_body_site}")
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
    
    def check_dosage_match(dosage_in_text: str, expected_dosages: list[Medication]) -> Medication:
        for expected_dosage in expected_dosages:
            #print(f"Checking expected dosage: {expected_dosage.Dosage} against text dosage: {dosage_in_text}")
            if re.search(re.escape(expected_dosage.Strength), dosage_in_text, re.IGNORECASE):
                return expected_dosage
        return None

    async def _do_eval(self, eval_input: Dict) -> Dict[str, Union[float, str]]:  # type: ignore[override]
        """Do a clinical safety evaluation.

        :param eval_input: The input to the evaluator. Expected to contain
        whatever inputs are needed for the _flow method, including context
        and other fields depending on the child class.
        :type eval_input: Dict
        :return: The evaluation result.
        :rtype: Dict
        """

        notes_drug_intolerance_list: list[DrugIntolerance] = []
        notes_medication_list: list[MedicationInNote] = []
        notes_medication_not_found_list: list[MedicationInNote] = []

        print(clinical_utils.drug_intolerance_lookup)
        print(clinical_utils.drug_intolerance_description)

        for medication_name, dosage_in_text in self.medication_to_dosage.items():
            medication_key = medication_name.lower()
            if medication_key in clinical_utils.medication_lookup:
                drugbank_id = clinical_utils.medication_lookup[medication_key]
                if drugbank_id in clinical_utils.drug_info_lookup:
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

        for i in range(len(notes_medication_list)):
            for j in range(i + 1, len(notes_medication_list)):
                print(notes_medication_list[i], notes_medication_list[j])
                drug_info = clinical_utils.drug_info_lookup[notes_medication_list[i].drugbank_id]
                if notes_medication_list[i].drugbank_id in clinical_utils.drug_intolerance_lookup[notes_medication_list[j].drugbank_id]:   
                    print(f"  Found drug intolerance between {notes_medication_list[i].medication} and {notes_medication_list[j].medication}")
                    print(f"  Drug Intolerance Info: {clinical_utils.drug_intolerance_description[f'{notes_medication_list[i].drugbank_id}_{notes_medication_list[j].drugbank_id}']}")
                    notes_drug_intolerance_list.append(DrugIntolerance(
                        drugbank_id=notes_medication_list[i].drugbank_id,
                        drug_name=notes_medication_list[i].medication,
                        intolerance_drugbank_id=notes_medication_list[j].drugbank_id,
                        intolerance_drug_name=notes_medication_list[j].medication,
                        description=clinical_utils.drug_intolerance_description[f"{notes_medication_list[i].drugbank_id}_{notes_medication_list[j].drugbank_id}"]
                    ))
                #print(f"Checking medication: {medication_name} with DrugBank ID: {drugbank_id}")
                # get medical intolerance info for all the drugs
                #print(f"  Expected Drug Info: {drug_info}")
                #drug_intolerance_combined_list.extend(drug_intolerance_lookup[drugbank_id])

        print(f"notes_drug_intolerance_list:{notes_drug_intolerance_list}")
        print(f"Diagnosis mapping:{self.diagnosis}")
        print(f"Conditions mapping:{self.conditions}")   
        print(f"age: {self.patientinfo_dict['age']}")     
        print(f"gender: {self.patientinfo_dict['gender']}")            

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