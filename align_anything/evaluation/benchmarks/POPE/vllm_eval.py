# Copyright 2024 PKU-Alignment Team. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import argparse
import re
from typing import Dict, List

from tqdm import tqdm

from align_anything.evaluation.data_type import InferenceInput, InferenceOutput
from align_anything.evaluation.dataloader.base_dataloader import BaseDataLoader
from align_anything.evaluation.eval_logger import EvalLogger
from align_anything.evaluation.inference.vllm_inference import BaseInferencer_vllm, os, save_detail
from align_anything.utils.template_registry import get_eval_template_class as get_template_class
from align_anything.utils.tools import (
    custom_cfgs_to_dict,
    dict_to_namedtuple,
    load_raw_outputs,
    read_eval_cfgs,
    save_raw_outputs,
    update_dict,
)
from datasets import DatasetDict, load_dataset


class POPEDataLoader(BaseDataLoader):
    def get_task_names(self):
        if isinstance(self.data_cfgs.task, list):
            return self.data_cfgs.task
        else:
            task_names = [self.data_cfgs.task]
            return task_names

    def get_answer(self, data):
        return data['answer']

    def build_example_prompt(self, data, with_answer=True):
        return f"{data['question']}"

    def build_prompt(self, data):
        assert self.num_shot == 0, 'POPE does not support few-shot learning.'
        prompt = ''
        template = get_template_class(self.chat_template)
        question = [
            template.system_prompt
            + template.user_prompt.format(input=prompt + self.build_example_prompt(item, False))
            + template.assistant_prompt.format(output='')
            for item in data
        ]

        return question

    def preprocess(self, data):
        return self.build_prompt(data)

    def load_dataset(self) -> DatasetDict:
        processed_inputs = {}
        for task in self.task_names:
            dataset = load_dataset(self.task_dir, self.split)[task]
            prompts = self.preprocess(dataset)
            processed_inputs[task] = []
            for prompt, image, question_id in zip(
                prompts, dataset['image'], dataset['question_id']
            ):
                processed_input = InferenceInput(text=prompt, image_file=image)
                processed_input.question_id = question_id
                processed_inputs[task].append(processed_input)
        return processed_inputs


class POPEGeneratorVLLM(BaseInferencer_vllm):
    def eval(
        self, data: Dict[str, List[InferenceInput]], eval_configs
    ) -> Dict[str, List[InferenceOutput]]:
        task2details = {}
        for task, input in data.items():
            raw_output = self.generation(input)
            for item in raw_output:
                item.prompt = re.sub(r'<image>', '', item.prompt)
                item.raw_output.prompt = re.sub(r'<image>', '', item.raw_output.prompt)
            task2details[task] = raw_output

        return task2details

    def _generation(self, inputs: List[InferenceInput]) -> List[InferenceOutput]:
        assert isinstance(inputs, list)
        InferenceOutputs = []
        outputs = self.model.generate(
            [
                {
                    'prompt': input.text,
                    'multi_modal_data': {'image': input.image_file},
                }
                for input in inputs
            ],
            sampling_params=self.samplingparams,
        )
        InferenceOutputs = [
            InferenceOutput.from_vllm_output(
                question_id=input.question_id, vllm_output=output, store_raw=True
            )
            for output, input in zip(outputs, inputs)
        ]
        return InferenceOutputs


def evaluator(test_dataset, output_data, file_path):
    num_sum = 0
    num_yes = 0
    question_id = set()

    TP, TN, FP, FN = 0, 0, 0, 0
    for test_item in tqdm(test_dataset, desc='Evaluating'):
        for output_item in output_data:
            if (
                test_item['question_id'] == output_item.question_id
                and output_item.question_id not in question_id
            ):
                question_id.add(output_item.question_id)
                num_sum += 1
                response = output_item.response[0].lower()
                correct_answer = test_item['answer'].lower()
                pred = judger(response)
                label = 1 if correct_answer == 'yes' else 0
                num_yes += pred

                if pred == 1 and label == 1:
                    TP += 1
                elif pred == 1 and label == 0:
                    FP += 1
                elif pred == 0 and label == 0:
                    TN += 1
                elif pred == 0 and label == 1:
                    FN += 1

                save_detail(test_item['question'], '', correct_answer, response, pred, file_path)

    precision = float(TP) / (TP + FP) if (TP + FP) > 0 else 0
    recall = float(TP) / (TP + FN) if (TP + FN) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    acc = (TP + TN) / (TP + TN + FP + FN) if (TP + TN + FP + FN) > 0 else 0
    yes_ratio = num_yes / num_sum if num_sum > 0 else 0

    result = {
        'accuracy': acc * 100,
        'precision': precision * 100,
        'recall': recall * 100,
        'f1_score': f1 * 100,
        'yes_ratio': yes_ratio * 100,
    }
    return result, num_sum


def judger(response):
    response = response.replace(',', '')
    words = response.split(' ')
    if 'not' in words or 'no' in words:
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    _, unparsed_args = parser.parse_known_args()
    keys = [k[2:] for k in unparsed_args[0::2]]
    values = list(unparsed_args[1::2])
    unparsed_args = dict(zip(keys, values))

    dict_configs, infer_configs = read_eval_cfgs('pope', 'vLLM')

    try:
        assert dict_configs or infer_configs, 'Config file does not exist or is incomplete.'
    except AssertionError:
        print('Config file is not exist or incomplete.')
        exit()

    for k, v in unparsed_args.items():
        if v == '' or v is None:
            continue
        dict_configs = update_dict(dict_configs, custom_cfgs_to_dict(k, v))
        infer_configs = update_dict(infer_configs, custom_cfgs_to_dict(k, v))

    dict_configs, infer_configs = dict_to_namedtuple(dict_configs), dict_to_namedtuple(
        infer_configs
    )
    model_config = dict_configs.default.model_cfgs
    data_cfgs = dict_configs.default.data_cfgs
    eval_configs = dict_configs.default.eval_cfgs
    logger = EvalLogger('Evaluation', log_dir=eval_configs.output_dir)
    dataloader = POPEDataLoader(dict_configs)
    assert not (
        dataloader.num_shot > 0 or dataloader.cot
    ), 'Few-shot or chain-of-thought cannot be used for this benchmark.'
    test_data = dataloader.load_dataset()
    eval_module = POPEGeneratorVLLM(model_config, infer_configs)
    raw_outputs_dir = os.path.join(
        eval_configs.output_dir,
        f"raw_outputs_{re.sub(r'/', '_', model_config.model_name_or_path)}.pkl",
    )
    if os.path.exists(raw_outputs_dir):
        raw_outputs = load_raw_outputs(raw_outputs_dir)
    else:
        raw_outputs = eval_module.eval(test_data, eval_configs)
        save_raw_outputs(raw_outputs, raw_outputs_dir)

    os.makedirs(logger.log_dir, exist_ok=True)
    uuid_path = f'{logger.log_dir}/{eval_configs.uuid}'
    os.makedirs(uuid_path, exist_ok=True)

    tot_accuracy, tot_precision, tot_recall, tot_f1_score, tot_yes_ratio = 0.0, 0.0, 0.0, 0.0, 0.0
    for task, _ in raw_outputs.items():
        test_data = load_dataset(data_cfgs.task_dir, data_cfgs.split)[task]
        file_path = f'{uuid_path}/{task}.json'
        result, num_sum = evaluator(test_data, raw_outputs[task], file_path)
        tot_accuracy += result['accuracy']
        tot_precision += result['precision']
        tot_recall += result['recall']
        tot_f1_score += result['f1_score']
        tot_yes_ratio += result['yes_ratio']

        output_dict = {
            'model_id': [dict_configs.default.model_cfgs.model_id],
            'num_sum': [num_sum],
            'accuracy': [result['accuracy']],
            'precision': [result['precision']],
            'recall': [result['recall']],
            'f1_score': [result['f1_score']],
            'yes_ratio': [result['yes_ratio']],
        }
        logger.print_table(title=f'POPE/{task} Benchmark', data=output_dict)
        logger.log('info', '+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
        logger.log('info', f'task: {task}')
        logger.log('info', f"model_id: {output_dict['model_id'][0]},")
        logger.log('info', f"num_sum: {output_dict['num_sum'][0]},")
        logger.log('info', f"accuracy: {output_dict['accuracy'][0]},")
        logger.log('info', f"precision: {output_dict['precision'][0]},")
        logger.log('info', f"recall: {output_dict['recall'][0]},")
        logger.log('info', f"f1_score: {output_dict['f1_score'][0]},")
        logger.log('info', f"yes_ratio: {output_dict['yes_ratio'][0]},")
        logger.log('info', '+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')

    output_dict = {
        'model_id': [dict_configs.default.model_cfgs.model_id],
        'num_sum': [num_sum],
        'tot_accuracy': [tot_accuracy / 3],
        'tot_precision': [tot_precision / 3],
        'tot_recall': [tot_recall / 3],
        'tot_f1_score': [tot_f1_score / 3],
        'tot_yes_ratio': [tot_yes_ratio / 3],
    }
    logger.print_table(title=f'POPE Benchmark', data=output_dict)
    logger.log('info', '+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
    logger.log('info', f'task: {task}')
    logger.log('info', f"model_id: {output_dict['model_id'][0]},")
    logger.log('info', f"num_sum: {output_dict['num_sum'][0]},")
    logger.log('info', f"tot_accuracy: {output_dict['tot_accuracy'][0]},")
    logger.log('info', f"tot_precision: {output_dict['tot_precision'][0]},")
    logger.log('info', f"tot_recall: {output_dict['tot_recall'][0]},")
    logger.log('info', f"tot_f1_score: {output_dict['tot_f1_score'][0]},")
    logger.log('info', f"tot_yes_ratio: {output_dict['tot_yes_ratio'][0]},")
    logger.log('info', '+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')


if __name__ == '__main__':
    main()
