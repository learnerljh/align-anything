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
import os
import pickle

from tqdm import tqdm

from align_anything.evaluation.eval_logger import EvalLogger
from align_anything.evaluation.inference.vllm_inference import re, save_detail
from align_anything.utils.tools import (
    custom_cfgs_to_dict,
    dict_to_namedtuple,
    read_eval_cfgs,
    update_dict,
)
from datasets import load_dataset


def load_pickle(file_path):
    with open(file_path, 'rb') as f:
        data = pickle.load(f)
    return data


def evaluator(test_dataset, output_data, file_path):
    num_match = 0
    num_sum = 0
    question_id = set()
    for test_item in tqdm(test_dataset, desc='Evaluating'):
        for output_item in output_data:
            if (
                test_item['question_id'] == output_item['question_id']
                and output_item['question_id'] not in question_id
            ):
                question_id.add(output_item['question_id'])
                num_sum += 1
                true_or_false = judger(
                    chr(test_item['correct_choice_idx'] + 65), output_item['response'][0]
                )
                true_or_false_loose = judger_loose(
                    test_item['choices'][test_item['correct_choice_idx']],
                    output_item['response'][0],
                )
                true_or_false = true_or_false or true_or_false_loose
                if true_or_false:
                    num_match += 1
                save_detail(
                    test_item['question'],
                    output_item['prompt_text'],
                    chr(test_item['correct_choice_idx'] + 65),
                    output_item['response'][0],
                    true_or_false,
                    file_path,
                )

    return num_match, num_sum


def judger(correct_answer, response):
    if correct_answer not in response:
        return False
    match = re.search(r'(?<![a-zA-Z])[A-Z](?![a-zA-Z])', response)
    if match:
        return correct_answer == match.group()
    return False


def judger_loose(correct_answer, response):
    if correct_answer.lower() in response.lower():
        return True
    return False


def main():
    cache_path = '.cache'
    assert os.path.exists(cache_path), '.cache folder not found. ds_infer failed?'

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    _, unparsed_args = parser.parse_known_args()
    keys = [k[2:] for k in unparsed_args[0::2]]
    values = list(unparsed_args[1::2])
    unparsed_args = dict(zip(keys, values))

    dict_configs, _ = read_eval_cfgs('a-okvqa', 'deepspeed')

    try:
        assert dict_configs, 'Config file does not exist or is incomplete.'
    except AssertionError:
        print('Config file is not exist or incomplete.')
        exit()

    for k, v in unparsed_args.items():
        if v == '' or v is None:
            continue
        dict_configs = update_dict(dict_configs, custom_cfgs_to_dict(k, v))

    dict_configs = dict_to_namedtuple(dict_configs)

    raw_outputs = {}
    uuid_path = os.path.join(cache_path, dict_configs.default.eval_cfgs.uuid)
    assert os.path.exists(uuid_path), 'uuid_path not found. ds_infer failed?'
    task_dirs = [
        (task, os.path.join(uuid_path, task))
        for task in os.listdir(uuid_path)
        if os.path.isdir(os.path.join(uuid_path, task))
    ]
    for task, task_dir in task_dirs:
        task_files = os.listdir(task_dir)
        InferenceOutputs = []
        for file in task_files:
            if file.endswith('.pkl'):
                file_path = os.path.join(task_dir, file)
                with open(file_path, 'rb') as f:
                    InferenceOutputs.extend(pickle.load(f))
        raw_outputs[task] = InferenceOutputs

    data_cfgs = dict_configs.default.data_cfgs
    eval_configs = dict_configs.default.eval_cfgs

    logger = EvalLogger('Align-Anything-Evaluation', dict_configs.default.eval_cfgs.output_dir)

    os.makedirs(logger.log_dir, exist_ok=True)
    uuid_path = f'{logger.log_dir}/{eval_configs.uuid}'
    os.makedirs(uuid_path, exist_ok=True)

    for task, _ in raw_outputs.items():
        test_data = load_dataset(data_cfgs.task_dir, task)[data_cfgs.split]

        file_path = f'{uuid_path}/{task}.json'
        num_match, num_sum = evaluator(test_data, raw_outputs[task], file_path)

        output_dict = {
            'model_id': [dict_configs.default.model_cfgs.model_id],
            'num_match': [num_match],
            'num_sum': [num_sum],
            'accuracy': [num_match / num_sum],
        }
        logger.print_table(title=f'A-OKVQA/{task} Benchmark ', data=output_dict)
        logger.log('info', '+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
        logger.log('info', f'task: {task}')
        logger.log('info', f"model_id: {output_dict['model_id'][0]},")
        logger.log('info', f"num_match: {output_dict['num_match'][0]},")
        logger.log('info', f"num_sum: {output_dict['num_sum'][0]},")
        logger.log('info', f"accuracy: {output_dict['accuracy'][0]},")
        logger.log('info', '+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')


if __name__ == '__main__':
    main()
