#!/usr/bin/env python3
"""
SFT 数据清洗与导出脚本

功能:
1. 从 eval_results.jsonl 读取原始评估数据
2. 应用严格的质量过滤规则
3. 转换为标准 SFT 训练格式
4. 导出为可直接用于训练的数据集

Author: Dexter
Date: 2026-01-28
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Tuple
from pathlib import Path

from evaluation.models import DataQualityTier
from evaluation.utils import is_chatty_query, has_code_indicators


# ============================================================================
# 配置
# ============================================================================

class CleaningConfig:
    """数据清洗配置"""
    # 质量阈值
    MIN_OVERALL_SCORE = DataQualityTier.min_score_for(DataQualityTier.SILVER)  # 统一阈值来源
    MIN_FAITHFULNESS = 0.6           # 最低 faithfulness
    MIN_ANSWER_RELEVANCE = 0.6       # 最低 answer_relevance
    
    # 长度阈值
    MIN_QUERY_LENGTH = 10            # 最短 query
    MIN_ANSWER_LENGTH = 100          # 最短 answer
    MIN_CONTEXT_LENGTH = 50          # 最短 context
    MAX_CONTEXT_LENGTH = 4000        # 最长 context（截断）
    
    # 必须条件
    REQUIRE_REPO_URL = True          # 必须有仓库 URL
    REQUIRE_CODE_IN_CONTEXT = True   # 上下文必须包含代码
    
    # 输出配置
    OUTPUT_DIR = "evaluation/sft_data/cleaned"


# ============================================================================
# 数据清洗逻辑
# ============================================================================

def validate_sample(sample: Dict, config: CleaningConfig) -> Tuple[bool, str]:
    """
    验证单个样本是否符合质量标准
    
    Returns:
        (is_valid, rejection_reason)
    """
    # 1. 检查基本字段存在
    if not sample.get("query"):
        return False, "missing_query"
    
    if not sample.get("generation"):
        return False, "missing_generation"
    
    gen = sample["generation"]
    
    # 2. 检查 repo_url
    if config.REQUIRE_REPO_URL and not sample.get("repo_url"):
        return False, "missing_repo_url"
    
    # 3. 检查质量分数
    overall_score = sample.get("overall_score", 0)
    if overall_score < config.MIN_OVERALL_SCORE:
        return False, f"low_score:{overall_score:.2f}"
    
    faithfulness = gen.get("faithfulness", 0)
    if faithfulness < config.MIN_FAITHFULNESS:
        return False, f"low_faithfulness:{faithfulness:.2f}"
    
    answer_relevance = gen.get("answer_relevance", 0)
    if answer_relevance < config.MIN_ANSWER_RELEVANCE:
        return False, f"low_relevance:{answer_relevance:.2f}"
    
    # 4. 检查长度
    query = sample.get("query", "")
    if len(query) < config.MIN_QUERY_LENGTH:
        return False, f"short_query:{len(query)}"
    
    answer = gen.get("generated_answer", "")
    if len(answer) < config.MIN_ANSWER_LENGTH:
        return False, f"short_answer:{len(answer)}"
    
    context = gen.get("retrieved_context", "")
    if len(context) < config.MIN_CONTEXT_LENGTH:
        return False, f"short_context:{len(context)}"
    
    # 5. 检查闲聊
    if is_chatty_query(query):
        return False, "chatty_query"
    
    # 6. 检查代码存在
    if config.REQUIRE_CODE_IN_CONTEXT and not has_code_indicators(context):
        return False, "no_code_in_context"
    
    return True, "passed"


def transform_to_sft_format(sample: Dict, config: CleaningConfig) -> Dict:
    """
    将原始评估数据转换为标准 SFT 格式
    """
    gen = sample["generation"]
    
    # 清理和截断 context
    context = gen.get("retrieved_context", "")
    if len(context) > config.MAX_CONTEXT_LENGTH:
        context = context[:config.MAX_CONTEXT_LENGTH] + "\n... [truncated]"
    
    # 构建标准 SFT 格式
    sft_sample = {
        # === 核心训练字段 ===
        "instruction": "你是一个专业的GitHub代码仓库分析助手。根据提供的代码上下文，准确回答用户关于代码实现、架构设计、功能逻辑等问题。回答时应该：1) 直接引用相关代码 2) 解释代码的工作原理 3) 如有必要，提供代码示例。",
        "input": f"[用户问题]\n{sample['query']}\n\n[代码上下文]\n{context}",
        "output": gen.get("generated_answer", ""),
        
        # === 元数据 ===
        "metadata": {
            "query": sample["query"],
            "repo_url": sample.get("repo_url", ""),
            "language": sample.get("language", "en"),
            "session_id": sample.get("session_id", ""),
            "timestamp": sample.get("timestamp", ""),
            "quality_tier": sample.get("data_quality_tier", ""),
            "overall_score": sample.get("overall_score", 0),
            "faithfulness": gen.get("faithfulness", 0),
            "answer_relevance": gen.get("answer_relevance", 0),
            "answer_completeness": gen.get("answer_completeness", 0),
            "code_correctness": gen.get("code_correctness", 0),
        }
    }
    
    return sft_sample


def clean_and_export(
    input_file: str = "evaluation/sft_data/eval_results.jsonl",
    config: CleaningConfig = None
) -> Dict:
    """
    清洗数据并导出
    
    Returns:
        统计信息
    """
    config = config or CleaningConfig()
    
    # 创建输出目录
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 统计
    stats = {
        "total_read": 0,
        "passed": 0,
        "rejected": 0,
        "rejection_reasons": {},
        "quality_distribution": {"gold": 0, "silver": 0, "bronze": 0, "rejected": 0}
    }
    
    # 输出文件
    output_file = output_dir / f"sft_train_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    rejected_file = output_dir / f"rejected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    
    print("=" * 60)
    print("🧹 SFT 数据清洗与导出")
    print("=" * 60)
    print(f"输入文件: {input_file}")
    print(f"输出目录: {output_dir}")
    print(f"质量阈值: score >= {config.MIN_OVERALL_SCORE}")
    print()
    
    if not os.path.exists(input_file):
        print(f"❌ 输入文件不存在: {input_file}")
        return stats
    
    passed_samples = []
    rejected_samples = []
    
    # 读取并处理
    with open(input_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            try:
                sample = json.loads(line)
                stats["total_read"] += 1
                
                # 验证
                is_valid, reason = validate_sample(sample, config)
                
                if is_valid:
                    # 转换格式
                    sft_sample = transform_to_sft_format(sample, config)
                    passed_samples.append(sft_sample)
                    stats["passed"] += 1
                    
                    # 统计质量分布
                    score = sample.get("overall_score", 0)
                    tier = DataQualityTier.from_score(score).value
                    if tier in stats["quality_distribution"]:
                        stats["quality_distribution"][tier] += 1
                else:
                    rejected_samples.append({
                        "reason": reason,
                        "query": sample.get("query", "")[:50],
                        "score": sample.get("overall_score", 0)
                    })
                    stats["rejected"] += 1
                    stats["rejection_reasons"][reason] = stats["rejection_reasons"].get(reason, 0) + 1
                    
            except json.JSONDecodeError as e:
                print(f"  ⚠️ 第 {line_num} 行 JSON 解析错误: {e}")
                continue
    
    # 写入通过的样本
    if passed_samples:
        with open(output_file, 'w', encoding='utf-8') as f:
            for sample in passed_samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        print(f"✅ 已导出 {len(passed_samples)} 条高质量样本到: {output_file}")
    
    # 写入拒绝的样本（用于分析）
    if rejected_samples:
        with open(rejected_file, 'w', encoding='utf-8') as f:
            for sample in rejected_samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        print(f"📝 已记录 {len(rejected_samples)} 条被拒绝样本到: {rejected_file}")
    
    # 打印统计
    print()
    print("=" * 60)
    print("📊 统计报告")
    print("=" * 60)
    print(f"总读取: {stats['total_read']}")
    print(f"通过:   {stats['passed']} ({stats['passed']/max(stats['total_read'],1)*100:.1f}%)")
    print(f"拒绝:   {stats['rejected']} ({stats['rejected']/max(stats['total_read'],1)*100:.1f}%)")
    print()
    print("质量分布:")
    print(f"  🥇 Gold (>=0.9):   {stats['quality_distribution']['gold']}")
    print(f"  🥈 Silver (>=0.7): {stats['quality_distribution']['silver']}")
    print(f"  🥉 Bronze (>=0.5): {stats['quality_distribution']['bronze']}")
    print()
    
    if stats["rejection_reasons"]:
        print("拒绝原因分布:")
        for reason, count in sorted(stats["rejection_reasons"].items(), key=lambda x: -x[1]):
            print(f"  - {reason}: {count}")
    
    print()
    print("=" * 60)
    
    return stats


def export_for_training(
    input_file: str,
    output_file: str,
    format_type: str = "alpaca"
) -> int:
    """
    将清洗后的数据导出为特定训练格式
    
    Args:
        input_file: 清洗后的 JSONL 文件
        output_file: 输出文件
        format_type: 格式类型 (alpaca, sharegpt, messages)
    
    Returns:
        导出的样本数量
    """
    samples = []
    
    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            sample = json.loads(line)
            
            if format_type == "alpaca":
                # Alpaca 格式（适用于 LLaMA-Factory 等）
                formatted = {
                    "instruction": sample["instruction"],
                    "input": sample["input"],
                    "output": sample["output"]
                }
            
            elif format_type == "sharegpt":
                # ShareGPT 格式
                formatted = {
                    "conversations": [
                        {"from": "system", "value": sample["instruction"]},
                        {"from": "human", "value": sample["input"]},
                        {"from": "gpt", "value": sample["output"]}
                    ]
                }
            
            elif format_type == "messages":
                # OpenAI messages 格式
                formatted = {
                    "messages": [
                        {"role": "system", "content": sample["instruction"]},
                        {"role": "user", "content": sample["input"]},
                        {"role": "assistant", "content": sample["output"]}
                    ]
                }
            
            else:
                formatted = sample
            
            samples.append(formatted)
    
    # 写入
    with open(output_file, 'w', encoding='utf-8') as f:
        if output_file.endswith('.json'):
            json.dump(samples, f, ensure_ascii=False, indent=2)
        else:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
    
    print(f"✅ 已导出 {len(samples)} 条样本为 {format_type} 格式: {output_file}")
    return len(samples)


# ============================================================================
# 主函数
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="SFT 数据清洗与导出工具")
    parser.add_argument("--input", "-i", default="evaluation/sft_data/eval_results.jsonl",
                       help="输入文件路径")
    parser.add_argument("--min-score", "-s", type=float, default=0.7,
                       help="最低质量分数 (默认: 0.7)")
    parser.add_argument("--format", "-f", choices=["alpaca", "sharegpt", "messages"],
                       default="alpaca", help="导出格式 (默认: alpaca)")
    parser.add_argument("--export", "-e", action="store_true",
                       help="同时导出为训练格式")
    
    args = parser.parse_args()
    
    # 配置
    config = CleaningConfig()
    config.MIN_OVERALL_SCORE = args.min_score
    
    # 清洗
    stats = clean_and_export(args.input, config)
    
    # 导出为训练格式
    if args.export and stats["passed"] > 0:
        # 找到最新的清洗文件
        output_dir = Path(config.OUTPUT_DIR)
        cleaned_files = sorted(output_dir.glob("sft_train_*.jsonl"), reverse=True)
        if cleaned_files:
            latest_file = cleaned_files[0]
            export_file = output_dir / f"train_{args.format}.jsonl"
            export_for_training(str(latest_file), str(export_file), args.format)
