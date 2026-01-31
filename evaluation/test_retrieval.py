#!/usr/bin/env python3
"""
检索系统离线评估脚本

用于测试 chunking 和检索策略的准确率。
使用 golden_dataset.json 中的标注数据作为 ground truth。

使用方法:
    python evaluation/test_retrieval.py --repo https://github.com/tiangolo/fastapi
    python evaluation/test_retrieval.py --repo https://github.com/tiangolo/fastapi --top-k 5
    python evaluation/test_retrieval.py --repo https://github.com/tiangolo/fastapi --verbose

Author: Dexter
Date: 2026-01-28
"""

import json
import os
import sys
import asyncio
import argparse
from typing import List, Dict, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.vector_service import store_manager
from app.services.github_service import get_repo_structure


@dataclass
class RetrievalTestResult:
    """单个测试用例的结果"""
    query: str
    expected_files: List[str]
    retrieved_files: List[str]
    hit: bool                      # 是否命中任意一个预期文件
    recall: float                  # 召回率: 命中的预期文件 / 总预期文件
    precision: float               # 精确率: 命中的预期文件 / 检索结果数
    reciprocal_rank: float         # 倒数排名: 1 / 第一个命中的位置
    difficulty: str = ""
    category: str = ""


@dataclass
class EvaluationReport:
    """完整评估报告"""
    repo_url: str
    top_k: int
    total_queries: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 聚合指标
    hit_rate: float = 0.0          # 命中率: 至少命中一个的查询比例
    mean_recall: float = 0.0       # 平均召回率
    mean_precision: float = 0.0    # 平均精确率
    mrr: float = 0.0               # Mean Reciprocal Rank
    
    # 按难度分组
    by_difficulty: Dict[str, Dict] = field(default_factory=dict)
    
    # 详细结果
    results: List[RetrievalTestResult] = field(default_factory=list)
    failed_cases: List[Dict] = field(default_factory=list)


class RetrievalEvaluator:
    """检索系统评估器"""
    
    def __init__(self, golden_dataset_path: str = "evaluation/golden_dataset.json"):
        self.golden_dataset = self._load_golden_dataset(golden_dataset_path)
        print(f"📊 Loaded {len(self.golden_dataset)} test cases from golden dataset")
    
    def _load_golden_dataset(self, path: str) -> List[Dict]:
        """加载黄金数据集"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Golden dataset not found: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    async def evaluate(
        self,
        repo_url: str,
        session_id: str = "eval_test",
        top_k: int = 5,
        verbose: bool = False
    ) -> EvaluationReport:
        """
        运行完整的检索评估
        
        Args:
            repo_url: 要评估的仓库 URL
            session_id: 会话 ID
            top_k: 每次检索返回的文件数
            verbose: 是否打印详细信息
        """
        print(f"\n{'='*60}")
        print(f"🔍 Retrieval Evaluation")
        print(f"{'='*60}")
        print(f"Repository: {repo_url}")
        print(f"Top-K: {top_k}")
        print(f"Test Cases: {len(self.golden_dataset)}")
        print(f"{'='*60}\n")
        
        # 获取仓库文件列表
        print("📂 Fetching repository structure...")
        file_list = get_repo_structure(repo_url)  # 同步函数，不需要 await
        print(f"   Found {len(file_list)} files")
        
        # 获取向量存储
        store = store_manager.get_store(session_id)
        chunk_count = store.collection.count()  # 使用 collection.count()
        if chunk_count == 0:
            print("\n⚠️  Vector store is empty!")
            print("   Please run the agent first to index the repository.")
            print("   Example: Access http://localhost:8000 and analyze the repo first.")
            return None
        print(f"   Vector store has {chunk_count} chunks")
        
        # 运行评估
        report = EvaluationReport(
            repo_url=repo_url,
            top_k=top_k,
            total_queries=len(self.golden_dataset)
        )
        
        hits = 0
        recalls = []
        precisions = []
        reciprocal_ranks = []
        
        difficulty_stats = {}
        
        for i, sample in enumerate(self.golden_dataset):
            query = sample.get("query", "")
            expected_files = sample.get("expected_files", [])
            difficulty = sample.get("difficulty", "medium")
            category = sample.get("category", "general")
            
            if not query or not expected_files:
                continue
            
            # 执行检索 (使用 hybrid search)
            try:
                results = await store.search_hybrid(query, top_k=top_k)
            except Exception as e:
                if verbose:
                    print(f"  [ERR] Search failed: {e}")
                continue
            
            # 提取检索到的文件路径
            retrieved_files = []
            for doc in results:
                if isinstance(doc, dict):
                    file_path = doc.get("file", "")
                    if file_path and file_path not in retrieved_files:
                        retrieved_files.append(file_path)
            
            # 计算指标
            expected_set = set(expected_files)
            retrieved_set = set(retrieved_files[:top_k])
            
            # 命中的文件
            hits_set = expected_set & retrieved_set
            
            # Hit: 是否命中任意一个
            hit = len(hits_set) > 0
            if hit:
                hits += 1
            
            # Recall: 命中的 / 期望的
            recall = len(hits_set) / len(expected_set) if expected_set else 0
            recalls.append(recall)
            
            # Precision: 命中的 / 检索的
            precision = len(hits_set) / min(len(retrieved_files), top_k) if retrieved_files else 0
            precisions.append(precision)
            
            # Reciprocal Rank: 1 / 第一个命中的位置
            rr = 0.0
            for rank, file in enumerate(retrieved_files[:top_k], 1):
                if file in expected_set:
                    rr = 1.0 / rank
                    break
            reciprocal_ranks.append(rr)
            
            # 记录结果
            result = RetrievalTestResult(
                query=query,
                expected_files=expected_files,
                retrieved_files=retrieved_files[:top_k],
                hit=hit,
                recall=recall,
                precision=precision,
                reciprocal_rank=rr,
                difficulty=difficulty,
                category=category
            )
            report.results.append(result)
            
            # 按难度统计
            if difficulty not in difficulty_stats:
                difficulty_stats[difficulty] = {"hits": 0, "total": 0, "recalls": [], "precisions": []}
            difficulty_stats[difficulty]["total"] += 1
            if hit:
                difficulty_stats[difficulty]["hits"] += 1
            difficulty_stats[difficulty]["recalls"].append(recall)
            difficulty_stats[difficulty]["precisions"].append(precision)
            
            # 记录失败案例
            if not hit:
                report.failed_cases.append({
                    "query": query,
                    "expected": expected_files,
                    "retrieved": retrieved_files[:top_k],
                    "difficulty": difficulty
                })
            
            # 打印进度
            if verbose:
                status = "✅" if hit else "❌"
                print(f"  [{i+1:3d}] {status} Recall={recall:.2f} | {query[:50]}...")
            else:
                print(f"\r  Progress: {i+1}/{len(self.golden_dataset)}", end="")
        
        print("\n")
        
        # 计算聚合指标
        report.hit_rate = hits / len(self.golden_dataset) if self.golden_dataset else 0
        report.mean_recall = sum(recalls) / len(recalls) if recalls else 0
        report.mean_precision = sum(precisions) / len(precisions) if precisions else 0
        report.mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0
        
        # 按难度汇总
        for diff, stats in difficulty_stats.items():
            report.by_difficulty[diff] = {
                "hit_rate": stats["hits"] / stats["total"] if stats["total"] else 0,
                "mean_recall": sum(stats["recalls"]) / len(stats["recalls"]) if stats["recalls"] else 0,
                "mean_precision": sum(stats["precisions"]) / len(stats["precisions"]) if stats["precisions"] else 0,
                "total": stats["total"]
            }
        
        return report
    
    def print_report(self, report: EvaluationReport):
        """打印评估报告"""
        print(f"\n{'='*60}")
        print(f"📊 RETRIEVAL EVALUATION REPORT")
        print(f"{'='*60}")
        print(f"Repository: {report.repo_url}")
        print(f"Top-K: {report.top_k}")
        print(f"Total Queries: {report.total_queries}")
        print(f"Timestamp: {report.timestamp}")
        print(f"{'='*60}\n")
        
        print("📈 OVERALL METRICS")
        print(f"   Hit Rate:       {report.hit_rate:.1%}")
        print(f"   Mean Recall:    {report.mean_recall:.1%}")
        print(f"   Mean Precision: {report.mean_precision:.1%}")
        print(f"   MRR:            {report.mrr:.3f}")
        
        print(f"\n📊 BY DIFFICULTY")
        for diff, stats in sorted(report.by_difficulty.items()):
            print(f"   {diff.upper():8s} | Hit: {stats['hit_rate']:.1%} | Recall: {stats['mean_recall']:.1%} | n={stats['total']}")
        
        if report.failed_cases:
            print(f"\n❌ FAILED CASES ({len(report.failed_cases)} total)")
            for case in report.failed_cases[:5]:  # 只显示前5个
                print(f"   Query: {case['query'][:60]}...")
                print(f"   Expected: {case['expected']}")
                print(f"   Got: {case['retrieved'][:3]}...")
                print()
        
        print(f"{'='*60}")
    
    def save_report(self, report: EvaluationReport, output_path: str = "evaluation/retrieval_report.json"):
        """保存报告到文件"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 转换为可序列化格式
        data = {
            "repo_url": report.repo_url,
            "top_k": report.top_k,
            "total_queries": report.total_queries,
            "timestamp": report.timestamp,
            "metrics": {
                "hit_rate": report.hit_rate,
                "mean_recall": report.mean_recall,
                "mean_precision": report.mean_precision,
                "mrr": report.mrr
            },
            "by_difficulty": report.by_difficulty,
            "failed_cases": report.failed_cases
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"\n💾 Report saved to: {output_path}")


async def main():
    parser = argparse.ArgumentParser(description="Evaluate retrieval system using golden dataset")
    parser.add_argument("--repo", required=True, help="GitHub repository URL to evaluate")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results to retrieve (default: 5)")
    parser.add_argument("--session", default="eval_test", help="Session ID for vector store")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed results")
    parser.add_argument("--save", action="store_true", help="Save report to file")
    
    args = parser.parse_args()
    
    evaluator = RetrievalEvaluator()
    report = await evaluator.evaluate(
        repo_url=args.repo,
        session_id=args.session,
        top_k=args.top_k,
        verbose=args.verbose
    )
    
    if report:
        evaluator.print_report(report)
        if args.save:
            evaluator.save_report(report)


if __name__ == "__main__":
    asyncio.run(main())
