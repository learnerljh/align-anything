#!/usr/bin/env bash
#
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


ACTOR_MODEL_NAME_OR_PATH="Qwen/Qwen2-VL-7B-Instruct" # model path
REWARD_MODEL_NAME_OR_PATH="../outputs/qwen2vl_rm" # model path
CRITIC_MODEL_NAME_OR_PATH="../outputs/qwen2vl_rm" # model path

TRAIN_DATASETS="PKU-Alignment/align-anything-400k" # dataset path
TRAIN_TEMPLATE="Qwen2-VL" # dataset template
TRAIN_NAME="text-video-to-text" # dataset name
TRAIN_SPLIT="train" # split the dataset

PTX_DATASETS="PKU-Alignment/align-anything-400k"
PTX_TEMPLATE="Qwen2-VL"
PTX_SPLIT="train"

OUTPUT_DIR="../outputs/qwen2vl_ppo" # output dir

# For wandb online logging
export WANDB_API_KEY=""

# Source the setup script
source ./setup.sh

# Execute deepspeed command
deepspeed \
     --master_port ${MASTER_PORT} \
     --module align_anything.trainers.text_video_to_text.ppo \
     --actor_model_name_or_path ${ACTOR_MODEL_NAME_OR_PATH} \
     --reward_model_name_or_path ${REWARD_MODEL_NAME_OR_PATH} \
     --reward_critic_model_name_or_path ${CRITIC_MODEL_NAME_OR_PATH} \
     --train_datasets ${TRAIN_DATASETS} \
     --train_template ${TRAIN_TEMPLATE} \
     --train_name ${TRAIN_NAME} \
     --train_split ${TRAIN_SPLIT} \
     --ptx_datasets ${PTX_DATASETS} \
     --ptx_template ${PTX_TEMPLATE} \
     --ptx_split ${PTX_SPLIT} \
     --output_dir ${OUTPUT_DIR} \
     --save_interval 1000 \
     --epochs 2
