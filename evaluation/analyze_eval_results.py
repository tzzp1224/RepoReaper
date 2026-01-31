# 文件路径: evaluation/analyze_eval_results.py
"""
自动化数据分析脚本
用于分析评估结果，识别问题并生成诊断报告

核心功能:
1. 自动读取所有评估结果
2. 按问题类型分类 Bad Case
3. 生成可视化报告
4. 推荐优化方向

Author: Dexter
Date: 2025-01-27
"""

import os
from typing import Dict, List
from collections import Counter, defaultdict
from datetime import datetime

from evaluation.utils import read_jsonl


class EvaluationAnalyzer:
    """评估结果分析器"""
    
    def __init__(self, eval_results_file: str = "evaluation/sft_data/eval_results.jsonl"):
        self.eval_results_file = eval_results_file
        self.results: List[Dict] = read_jsonl(eval_results_file)
        if not self.results:
            print(f"⚠️ No results loaded from: {eval_results_file}")
    
    def get_basic_stats(self) -> Dict:
        """获取基本统计"""
        if not self.results:
            return {}
        
        scores = [r.get("overall_score", 0) for r in self.results]
        tiers = [r.get("data_quality_tier", "unknown") for r in self.results]
        
        return {
            "total_evaluations": len(self.results),
            "avg_score": sum(scores) / len(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "min_score": min(scores) if scores else 0,
            "median_score": sorted(scores)[len(scores)//2] if scores else 0,
            "quality_distribution": dict(Counter(tiers)),
            "sft_ready_count": sum(1 for r in self.results if r.get("sft_ready", False))
        }
    
    def identify_bad_cases(self, threshold: float = 0.6) -> List[Dict]:
        """
        识别 Bad Case (得分低于阈值的结果)
        返回按得分排序的结果
        """
        bad_cases = [r for r in self.results if r.get("overall_score", 1) < threshold]
        return sorted(bad_cases, key=lambda x: x.get("overall_score", 1))
    
    def categorize_failures(self) -> Dict[str, List[Dict]]:
        """
        按失败原因分类 Bad Case
        
        失败类型:
        - retrieval_failure: 检索未命中
        - generation_hallucination: 生成幻觉
        - generation_incomplete: 回答不完整
        - tool_call_error: 工具调用失败
        """
        categorized = defaultdict(list)
        
        for result in self.identify_bad_cases():
            reasons = []
            
            # 检查检索失败
            if result.get("retrieval"):
                retrieval = result["retrieval"]
                if retrieval.get("hit_rate", 1) == 0:
                    reasons.append("retrieval_failure")
                elif retrieval.get("recall_at_k", 1) < 0.5:
                    reasons.append("retrieval_low_recall")
            
            # 检查生成问题
            if result.get("generation"):
                generation = result["generation"]
                if generation.get("faithfulness", 1) < 0.5:
                    reasons.append("generation_hallucination")
                if generation.get("answer_completeness", 1) < 0.4:
                    reasons.append("generation_incomplete")
                if generation.get("hallucination_count", 0) > 0:
                    reasons.append("hallucination_detected")
            
            # 检查Agent行为
            if result.get("agentic"):
                agentic = result["agentic"]
                if not agentic.get("success", True):
                    reasons.append("agentic_failure")
            
            # 如果没有具体原因,标记为unknown
            if not reasons:
                reasons.append("unknown")
            
            for reason in reasons:
                categorized[reason].append(result)
        
        return dict(categorized)
    
    def layer_performance(self) -> Dict[str, Dict]:
        """分析各层性能"""
        layer_scores = defaultdict(list)
        
        for result in self.results:
            if result.get("query_rewrite"):
                score = result["query_rewrite"].get("overall_score", 0)
                if score:
                    layer_scores["query_rewrite"].append(score)
            
            if result.get("retrieval"):
                score = result["retrieval"].get("overall_score", 0)
                if score:
                    layer_scores["retrieval"].append(score)
            
            if result.get("generation"):
                score = result["generation"].get("overall_score", 0)
                if score:
                    layer_scores["generation"].append(score)
            
            if result.get("agentic"):
                score = result["agentic"].get("overall_score", 0)
                if score:
                    layer_scores["agentic"].append(score)
        
        # 计算每层的统计
        layer_stats = {}
        for layer, scores in layer_scores.items():
            if scores:
                layer_stats[layer] = {
                    "avg": sum(scores) / len(scores),
                    "min": min(scores),
                    "max": max(scores),
                    "count": len(scores)
                }
        
        return layer_stats
    
    def get_recommendations(self) -> List[str]:
        """基于分析结果生成优化建议"""
        recommendations = []
        
        # 分析各层性能
        layer_perf = self.layer_performance()
        
        # 检索层分析
        if "retrieval" in layer_perf:
            retrieval_score = layer_perf["retrieval"]["avg"]
            if retrieval_score < 0.7:
                recommendations.append(
                    "🔴 RETRIEVAL 层性能差 (avg: {:.2f})\n"
                    "  建议:\n"
                    "  1. 检查 chunking 策略是否过度分割\n"
                    "  2. 优化 embedding 模型 (考虑更强的模型)\n"
                    "  3. 调整混合检索的权重 (BM25 vs Vector)\n"
                    "  4. 分析实际召回的文件,看是否与预期偏离".format(retrieval_score)
                )
        
        # 生成层分析
        if "generation" in layer_perf:
            gen_score = layer_perf["generation"]["avg"]
            if gen_score < 0.7:
                recommendations.append(
                    "🟡 GENERATION 层存在问题 (avg: {:.2f})\n"
                    "  建议:\n"
                    "  1. 检查 Prompt 是否清晰 (可能LLM理解偏差)\n"
                    "  2. 检查是否存在幻觉 (生成不存在的函数名等)\n"
                    "  3. 优化 Context 的组织方式\n"
                    "  4. 考虑使用更强的LLM模型".format(gen_score)
                )
        
        # Query Rewrite 分析
        if "query_rewrite" in layer_perf:
            rewrite_score = layer_perf["query_rewrite"]["avg"]
            if rewrite_score < 0.6:
                recommendations.append(
                    "🟠 QUERY_REWRITE 层准确度低 (avg: {:.2f})\n"
                    "  建议:\n"
                    "  1. 优化关键词提取 Prompt\n"
                    "  2. 增加多语言处理支持\n"
                    "  3. 添加领域词汇表 (Domain Vocabulary)".format(rewrite_score)
                )
        
        # 通用建议
        stats = self.get_basic_stats()
        if stats.get("sft_ready_count", 0) / max(stats.get("total_evaluations", 1), 1) < 0.5:
            recommendations.append(
                "⚠️ SFT 可用数据不足 (< 50%)\n"
                "  立即行动:\n"
                "  1. 运行 continuous_eval 脚本收集更多数据\n"
                "  2. 对现有数据进行自纠正 (Self-Correction)\n"
                "  3. 扩展黄金数据集来改进模型"
            )
        
        return recommendations
    
    def generate_report(self, output_file: str = "evaluation/analysis_report.md") -> str:
        """生成完整的分析报告"""
        
        report = []
        report.append("# 📊 GitHub Agent 评估分析报告\n")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report.append("---\n")
        
        # 1. 基本统计
        stats = self.get_basic_stats()
        report.append("## 📈 基本统计\n")
        report.append(f"- 总评估次数: {stats.get('total_evaluations', 0)}\n")
        report.append(f"- 平均得分: {stats.get('avg_score', 0):.3f}\n")
        report.append(f"- 最高得分: {stats.get('max_score', 0):.3f}\n")
        report.append(f"- 最低得分: {stats.get('min_score', 0):.3f}\n")
        report.append(f"- 中位数得分: {stats.get('median_score', 0):.3f}\n")
        report.append(f"- SFT 可用样本: {stats.get('sft_ready_count', 0)}\n\n")
        
        # 2. 质量分级分布
        report.append("## 🏆 质量分级分布\n")
        distribution = stats.get("quality_distribution", {})
        for tier, count in sorted(distribution.items()):
            percentage = (count / stats.get('total_evaluations', 1)) * 100
            report.append(f"- {tier.upper()}: {count} ({percentage:.1f}%)\n")
        report.append("\n")
        
        # 3. 各层性能
        report.append("## 🎯 各层性能分析\n\n")
        layer_perf = self.layer_performance()
        for layer in ["query_rewrite", "retrieval", "generation", "agentic"]:
            if layer in layer_perf:
                perf = layer_perf[layer]
                report.append(f"### {layer.upper()}\n")
                report.append(f"- 平均得分: {perf['avg']:.3f}\n")
                report.append(f"- 范围: [{perf['min']:.3f}, {perf['max']:.3f}]\n")
                report.append(f"- 样本数: {perf['count']}\n\n")
        
        # 4. Bad Case 分类
        report.append("## 🔴 Bad Case 分析\n\n")
        failures = self.categorize_failures()
        for reason, cases in sorted(failures.items(), key=lambda x: -len(x[1])):
            report.append(f"### {reason} ({len(cases)} cases)\n")
            for case in cases[:3]:  # 显示top 3
                report.append(f"- 查询: {case.get('query', 'N/A')[:60]}...\n")
                report.append(f"  得分: {case.get('overall_score', 0):.3f}\n")
        report.append("\n")
        
        # 5. 推荐行动
        report.append("## 💡 优化建议\n\n")
        recommendations = self.get_recommendations()
        for i, rec in enumerate(recommendations, 1):
            report.append(f"{i}. {rec}\n\n")
        
        # 写入文件
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(report)
        
        return "".join(report)
    
    def export_bad_cases_csv(self, output_file: str = "evaluation/bad_cases.csv") -> None:
        """导出 Bad Case 为 CSV (用于人工审查)"""
        import csv
        
        bad_cases = self.identify_bad_cases()
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                "query", "overall_score", "tier",
                "retrieval_score", "generation_score", "agentic_score",
                "error_message", "timestamp"
            ])
            
            writer.writeheader()
            for case in bad_cases:
                writer.writerow({
                    "query": case.get("query", ""),
                    "overall_score": case.get("overall_score", 0),
                    "tier": case.get("data_quality_tier", "unknown"),
                    "retrieval_score": case.get("retrieval", {}).get("overall_score", 0),
                    "generation_score": case.get("generation", {}).get("overall_score", 0),
                    "agentic_score": case.get("agentic", {}).get("overall_score", 0),
                    "error_message": case.get("error_message", ""),
                    "timestamp": case.get("timestamp", "")
                })
        
        print(f"✅ Exported {len(bad_cases)} bad cases to {output_file}")


# ============================================================================
# 命令行工具
# ============================================================================

def print_summary(analyzer: EvaluationAnalyzer):
    """打印摘要"""
    print("\n" + "=" * 70)
    print("📊 评估结果摘要")
    print("=" * 70)
    
    stats = analyzer.get_basic_stats()
    
    print(f"\n📈 基本统计:")
    print(f"  总评估: {stats.get('total_evaluations', 0)}")
    print(f"  平均分: {stats.get('avg_score', 0):.3f}")
    print(f"  分布: {stats.get('quality_distribution', {})}")
    print(f"  SFT可用: {stats.get('sft_ready_count', 0)}")
    
    print(f"\n🎯 各层性能:")
    layer_perf = analyzer.layer_performance()
    for layer, perf in layer_perf.items():
        print(f"  {layer:.<30} {perf['avg']:.3f} (avg)")
    
    print(f"\n🔴 Bad Case Top 5:")
    bad_cases = analyzer.identify_bad_cases()[:5]
    for i, case in enumerate(bad_cases, 1):
        print(f"  {i}. {case.get('query', 'N/A')[:40]:<40} Score: {case.get('overall_score', 0):.3f}")
    
    print(f"\n💡 优化建议:")
    recommendations = analyzer.get_recommendations()
    for rec in recommendations[:3]:
        print(f"  - {rec.split(chr(10))[0]}")
    
    print("\n" + "=" * 70)


def main():
    import sys
    
    analyzer = EvaluationAnalyzer()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "summary":
            print_summary(analyzer)
        
        elif command == "report":
            report = analyzer.generate_report()
            print(report)
        
        elif command == "bad-cases":
            analyzer.export_bad_cases_csv()
            bad_cases = analyzer.identify_bad_cases()
            print(f"\n✅ Found {len(bad_cases)} bad cases")
            print("详见 evaluation/bad_cases.csv")
        
        elif command == "layer-perf":
            layer_perf = analyzer.layer_performance()
            print("\n🎯 各层性能:")
            for layer, perf in layer_perf.items():
                print(f"\n{layer.upper()}:")
                print(f"  Average: {perf['avg']:.3f}")
                print(f"  Range: [{perf['min']:.3f}, {perf['max']:.3f}]")
                print(f"  Samples: {perf['count']}")
        
        elif command == "recommendations":
            recs = analyzer.get_recommendations()
            print("\n💡 优化建议:\n")
            for i, rec in enumerate(recs, 1):
                print(f"{i}.\n{rec}\n")
        
        else:
            print(f"Unknown command: {command}")
    
    else:
        print("自动化评估数据分析工具")
        print()
        print("用法:")
        print("  python analyze_eval_results.py summary         # 快速摘要")
        print("  python analyze_eval_results.py report          # 生成完整报告")
        print("  python analyze_eval_results.py bad-cases       # 导出Bad Case")
        print("  python analyze_eval_results.py layer-perf      # 各层性能分析")
        print("  python analyze_eval_results.py recommendations # 优化建议")


if __name__ == "__main__":
    main()
