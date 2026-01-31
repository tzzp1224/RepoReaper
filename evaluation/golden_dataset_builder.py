# 文件路径: evaluation/golden_dataset_builder.py
"""
黄金数据集构建工具
用于快速构建评估所需的标注数据集

使用场景:
1. 初始化: 为新项目快速创建 50 条测试用例
2. 扩展: 定期添加新的问题和标注
3. 验证: 自动验证数据集的完整性

Author: Dexter
Date: 2025-01-27
"""

import json
import os
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class GoldenSample:
    """黄金数据集样本"""
    id: str                           # 唯一ID
    description: str                  # 问题描述 (用于标注人员理解问题类型)
    query: str                        # 用户查询
    expected_files: List[str]         # 标准答案: 应该返回的文件列表
    expected_answer: str = ""         # 标准答案: 预期回答 (可选)
    difficulty: str = "medium"        # 难度: easy/medium/hard
    category: str = "general"         # 类别: general/code_finding/architecture/workflow
    language: str = "en"              # 语言: en/zh
    created_at: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class GoldenDatasetBuilder:
    """黄金数据集构建器"""
    
    def __init__(self, filepath: str = "evaluation/golden_dataset.json"):
        self.filepath = filepath
        self.samples: List[GoldenSample] = []
        self.load()
    
    def load(self):
        """加载现有数据集"""
        if os.path.exists(self.filepath):
            with open(self.filepath, 'r', encoding='utf-8') as f:
                try:
                    raw_data = json.load(f)
                    # 兼容旧格式 (直接是字典列表)
                    if isinstance(raw_data, list):
                        self.samples = [
                            GoldenSample(**item) if isinstance(item, dict) and "id" in item
                            else GoldenSample(
                                id=str(len(self.samples)),
                                description=item.get("description", ""),
                                query=item.get("query", ""),
                                expected_files=[item.get("answer_file", "")] if item.get("answer_file") else []
                            )
                            for item in raw_data
                        ]
                except:
                    self.samples = []
    
    def save(self):
        """保存数据集"""
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        data = [asdict(s) for s in self.samples]
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def add_sample(self, sample: GoldenSample):
        """添加样本"""
        sample.id = f"sample_{len(self.samples):04d}"
        self.samples.append(sample)
    
    def add_samples_batch(self, samples: List[GoldenSample]):
        """批量添加样本"""
        for sample in samples:
            self.add_sample(sample)
    
    def get_samples_by_category(self, category: str) -> List[GoldenSample]:
        """按类别筛选"""
        return [s for s in self.samples if s.category == category]
    
    def get_samples_by_difficulty(self, difficulty: str) -> List[GoldenSample]:
        """按难度筛选"""
        return [s for s in self.samples if s.difficulty == difficulty]
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        stats = {
            "total": len(self.samples),
            "by_category": {},
            "by_difficulty": {},
            "by_language": {}
        }
        
        for s in self.samples:
            stats["by_category"][s.category] = stats["by_category"].get(s.category, 0) + 1
            stats["by_difficulty"][s.difficulty] = stats["by_difficulty"].get(s.difficulty, 0) + 1
            stats["by_language"][s.language] = stats["by_language"].get(s.language, 0) + 1
        
        return stats


# ============================================================================
# 预定义的通用问题模板
# ============================================================================

# 针对 FastAPI 项目的初始数据集 (参考你现有的 golden_dataset.json)
FASTAPI_GOLDEN_SAMPLES = [
    # Easy: 代码位置查找
    GoldenSample(
        id="",
        description="简单函数查找",
        query="Where is the 'serialize_response' function?",
        expected_files=["fastapi/routing.py"],
        difficulty="easy",
        category="code_finding"
    ),
    
    # Medium: 理解数据流
    GoldenSample(
        id="",
        description="理解核心模块职责",
        query="How does dependency injection work in FastAPI?",
        expected_files=["fastapi/dependencies/utils.py", "fastapi/depends.py"],
        difficulty="medium",
        category="architecture"
    ),
    
    # Hard: 跨文件理解工作流
    GoldenSample(
        id="",
        description="完整工作流理解",
        query="Show me the complete flow from request to response in FastAPI",
        expected_files=["fastapi/routing.py", "fastapi/applications.py", "fastapi/dependencies/utils.py"],
        difficulty="hard",
        category="workflow"
    ),
]

# GitHub Agent 项目的初始数据集
GITHUB_AGENT_GOLDEN_SAMPLES = [
    GoldenSample(
        id="",
        description="检索核心逻辑",
        query="How is chunk_file method implemented?",
        expected_files=["app/services/chunking_service.py"],
        expected_answer="The chunk_file method is implemented in chunking_service.py. It takes content and file_path as parameters and uses AST parsing for Python files to intelligently chunk the code.",
        difficulty="easy",
        category="code_finding",
        language="en"
    ),
    
    GoldenSample(
        id="",
        description="向量搜索机制",
        query="What vector database is used for retrieval?",
        expected_files=["app/services/vector_service.py"],
        difficulty="medium",
        category="architecture",
        language="en"
    ),
    
    GoldenSample(
        id="",
        description="完整分析流程",
        query="How does the agent analyze a GitHub repository?",
        expected_files=["app/services/agent_service.py", "app/services/chunking_service.py", "app/services/vector_service.py"],
        difficulty="hard",
        category="workflow",
        language="en"
    ),
]


# ============================================================================
# 交互式数据集构建工具
# ============================================================================

def interactive_builder():
    """交互式构建黄金数据集"""
    builder = GoldenDatasetBuilder()
    
    print("=" * 60)
    print("🛠️  黄金数据集构建工具")
    print("=" * 60)
    
    while True:
        print("\n请选择操作:")
        print("1. 添加新样本")
        print("2. 查看现有样本")
        print("3. 按类别筛选")
        print("4. 统计信息")
        print("5. 保存并退出")
        print("0. 退出(不保存)")
        
        choice = input("请输入选项 (0-5): ").strip()
        
        if choice == "1":
            sample = GoldenSample(
                id="",
                description=input("📝 描述 (问题类型): "),
                query=input("❓ 查询/问题: "),
                expected_files=input("📁 预期文件 (逗号分隔): ").split(","),
                expected_answer=input("📄 标准答案 (可选): "),
                difficulty=input("⭐ 难度 (easy/medium/hard) [medium]: ") or "medium",
                category=input("🏷️  类别 (code_finding/architecture/workflow/general) [general]: ") or "general",
                language=input("🌍 语言 (en/zh) [en]: ") or "en"
            )
            builder.add_sample(sample)
            print("✅ 样本已添加")
        
        elif choice == "2":
            print(f"\n总共 {len(builder.samples)} 个样本:")
            for s in builder.samples[-10:]:  # 显示最后10个
                print(f"  - [{s.difficulty}] {s.query[:50]}")
        
        elif choice == "3":
            category = input("输入类别: ")
            samples = builder.get_samples_by_category(category)
            print(f"\n找到 {len(samples)} 个 '{category}' 类别的样本:")
            for s in samples:
                print(f"  - {s.query}")
        
        elif choice == "4":
            stats = builder.get_statistics()
            print(f"\n📊 数据集统计:")
            print(f"  总样本数: {stats['total']}")
            print(f"  按类别: {stats['by_category']}")
            print(f"  按难度: {stats['by_difficulty']}")
            print(f"  按语言: {stats['by_language']}")
        
        elif choice == "5":
            builder.save()
            print("✅ 数据集已保存")
            break
        
        elif choice == "0":
            print("⚠️ 未保存,退出")
            break


# ============================================================================
# 自动评估数据集的完整性
# ============================================================================

def validate_golden_dataset(filepath: str = "evaluation/golden_dataset.json") -> Dict:
    """验证黄金数据集的完整性"""
    
    builder = GoldenDatasetBuilder(filepath)
    issues = {
        "missing_fields": [],
        "empty_queries": [],
        "empty_files": [],
        "duplicates": []
    }
    
    seen_queries = set()
    
    for i, sample in enumerate(builder.samples):
        # 检查必填字段
        if not sample.query:
            issues["empty_queries"].append(f"Sample {i}: query is empty")
        
        if not sample.expected_files or all(not f for f in sample.expected_files):
            issues["empty_files"].append(f"Sample {i}: expected_files is empty")
        
        # 检查重复
        if sample.query in seen_queries:
            issues["duplicates"].append(f"Sample {i}: duplicate query")
        seen_queries.add(sample.query)
    
    return {
        "valid": len(issues) == 0 or not any(issues.values()),
        "total_samples": len(builder.samples),
        "issues": issues,
        "stats": builder.get_statistics()
    }


# ============================================================================
# 快速初始化脚本
# ============================================================================

def init_github_agent_dataset():
    """快速初始化 GitHub Agent 项目的数据集"""
    builder = GoldenDatasetBuilder("evaluation/golden_dataset.json")
    
    # 清空现有 (可选)
    # builder.samples = []
    
    # 添加初始样本
    builder.add_samples_batch(GITHUB_AGENT_GOLDEN_SAMPLES)
    
    # 额外添加更多样本 (扩展到30+)
    extra_samples = [
        GoldenSample(
            id="",
            description="向量检索质量",
            query="What retrieval metrics are tracked?",
            expected_files=["evaluation/evaluation_framework.py"],
            difficulty="medium",
            category="architecture"
        ),
        GoldenSample(
            id="",
            description="Agent决策过程",
            query="How does the agent decide which files to read?",
            expected_files=["app/services/agent_service.py"],
            difficulty="hard",
            category="workflow"
        ),
        GoldenSample(
            id="",
            description="错误处理",
            query="Where are network timeout errors handled?",
            expected_files=["app/services/agent_service.py", "app/services/chat_service.py"],
            difficulty="medium",
            category="code_finding"
        ),
    ]
    builder.add_samples_batch(extra_samples)
    builder.save()
    
    print(f"✅ 初始化完成: {len(builder.samples)} 个样本")
    print(f"📊 {builder.get_statistics()}")


# ============================================================================
# 导出为 Ragas 格式
# ============================================================================

def export_to_ragas_format(golden_filepath: str, output_filepath: str = "evaluation/ragas_eval_dataset.json"):
    """
    将黄金数据集导出为 Ragas 评估框架所需的格式
    
    Ragas 格式:
    {
        "questions": [...],
        "contexts": [...],
        "ground_truths": [...]
    }
    """
    builder = GoldenDatasetBuilder(golden_filepath)
    
    ragas_data = {
        "questions": [],
        "contexts": [],
        "ground_truths": [],
        "metadata": []
    }
    
    for sample in builder.samples:
        ragas_data["questions"].append(sample.query)
        ragas_data["ground_truths"].append({
            "answer": sample.expected_answer,
            "files": sample.expected_files
        })
        ragas_data["contexts"].append("\n".join(sample.expected_files))
        ragas_data["metadata"].append({
            "difficulty": sample.difficulty,
            "category": sample.category,
            "description": sample.description
        })
    
    os.makedirs(os.path.dirname(output_filepath), exist_ok=True)
    with open(output_filepath, 'w', encoding='utf-8') as f:
        json.dump(ragas_data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Exported to {output_filepath}")
    print(f"   Questions: {len(ragas_data['questions'])}")


# ============================================================================
# 命令行接口
# ============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "init":
            init_github_agent_dataset()
        
        elif command == "validate":
            result = validate_golden_dataset()
            print(json.dumps(result, indent=2, ensure_ascii=False))
        
        elif command == "export-ragas":
            export_to_ragas_format("evaluation/golden_dataset.json")
        
        elif command == "interactive":
            interactive_builder()
        
        else:
            print(f"Unknown command: {command}")
    
    else:
        print("黄金数据集构建工具")
        print()
        print("用法:")
        print("  python golden_dataset_builder.py init              # 快速初始化")
        print("  python golden_dataset_builder.py validate          # 验证数据集")
        print("  python golden_dataset_builder.py export-ragas      # 导出为Ragas格式")
        print("  python golden_dataset_builder.py interactive       # 交互式构建")
