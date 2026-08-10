#!/usr/bin/env python
"""检索模式 A/B 对比 -- 同一测试集分别用 vector / bm25 / hybrid 跑检索,对比命中率.

用法:
    python scripts/compare_retrieval_modes.py
    python scripts/compare_retrieval_modes.py data/eval/test_set_hard_retrieval.jsonl
    python scripts/compare_retrieval_modes.py --output data/eval/retrieval_ab.json

指标:
    recall@k -- 期望来源文件是否出现在检索结果前 k 个来源中(k = 1/3/5/10)

说明:
    - 路由使用关键词层(use_llm=False),不依赖 LLM API,可离线运行
    - FAQ 快速路径是 hybrid 的一部分(vector 模式会跳过,保证对照公平)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def init_system() -> dict:
    """加载 Orchestrator + 四个专家 Agent(索引已存在时只加载)."""
    from src.agents.orchestrator import Orchestrator
    from src.agents.base_agent import BaseAgent
    from src.rag.embedder import Embedder
    from src.rag.knowledge_base import KnowledgeBase
    from src.tools.hr_tools import HR_TOOLS
    from src.tools.it_tools import IT_TOOLS
    from src.tools.legal_tools import LEGAL_TOOLS
    from src.tools.finance_tools import FINANCE_TOOLS
    from src.config import config as cfg

    orchestrator = Orchestrator(None)  # 关键词层路由不需要 client
    embedder = Embedder()
    tool_map = {
        "HR_TOOLS": HR_TOOLS,
        "IT_TOOLS": IT_TOOLS,
        "LEGAL_TOOLS": LEGAL_TOOLS,
        "FINANCE_TOOLS": FINANCE_TOOLS,
    }
    agents: dict = {}
    for kb_spec in cfg.kb_registry:
        kb = KnowledgeBase(kb_spec["name"], kb_spec["dir"], embedder=embedder)
        if kb.is_index_stale():
            kb.build_index()
        else:
            kb.load_index()
        agent = BaseAgent(
            name=kb_spec["agent_name"],
            tools=tool_map[kb_spec["tools_import"]],
            kb=kb,
            client=None,
            model=cfg.llm_model,
        )
        agents[kb_spec["agent_key"]] = agent
    return {"orchestrator": orchestrator, "agents": agents}


def load_cases(path: str) -> list[dict]:
    from src.evaluation.loader import TestLoader

    return [c.model_dump() for c in TestLoader(path).load()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval mode A/B comparison")
    parser.add_argument("test_set", nargs="?", default="data/eval/test_set_hard_retrieval.jsonl")
    parser.add_argument("--output", default=None, help="Write JSON report")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    cases = load_cases(args.test_set)
    if not cases:
        print("❌ No test cases.")
        sys.exit(1)

    print(f"📋 Loaded {len(cases)} cases from {args.test_set}")
    system = init_system()
    orch = system["orchestrator"]
    agents = system["agents"]
    modes = ["vector", "bm25", "hybrid"]

    # per-mode: hits[k] = #cases where expected source in top-k
    hits = {m: {k: 0 for k in (1, 3, 5, args.top_k)} for m in modes}
    total = {m: 0 for m in modes}
    details: list[dict] = []

    for case in cases:
        route = orch.route(case["query"], use_llm=False)
        primary = route.get("primary", "fallback")
        agent = agents.get(primary)
        if agent is None:
            print(f"  ⏭  {case['id']}: routed to {primary}, skipped")
            continue
        expected = set(case.get("expected_sources") or [])
        row = {
            "id": case["id"],
            "query": case["query"],
            "agent": primary,
            "expected_sources": sorted(expected),
        }
        for mode in modes:
            chunks = agent.kb.query(case["query"], top_k=args.top_k, rewrite=False, mode=mode)
            sources = [c.get("source", "") for c in chunks]
            total[mode] += 1
            for k in (1, 3, 5, args.top_k):
                if expected and expected & set(sources[:k]):
                    hits[mode][k] += 1
            row[mode] = {"sources": sources[:5], "hit": bool(expected & set(sources))}
        details.append(row)

    print("\n" + "=" * 74)
    print("  Recall@k  (期望来源出现在检索结果前 k 个来源中的比例)")
    print("=" * 74)
    header = f"{'mode':<10}" + "".join(f"recall@{k:<7}" for k in (1, 3, 5, args.top_k)) + "cases"
    print(header)
    for mode in modes:
        cells = "".join(f"{hits[mode][k] / max(total[mode], 1):<12.1%}" for k in (1, 3, 5, args.top_k))
        print(f"{mode:<10}{cells}{total[mode]}")

    print("\nMisses at recall@10:")
    for row in details:
        if any(not row[m]["hit"] for m in modes):
            miss = [m for m in modes if not row[m]["hit"]]
            print(f"  {row['id']} [{row['agent']}] {row['query']}  miss: {miss}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"hits": hits, "total": total, "details": details}, f, ensure_ascii=False, indent=2)
        print(f"\n📄 Report written to {args.output}")

    for agent in agents.values():
        try:
            agent.kb.stop_watch()
        except Exception:
            pass


if __name__ == "__main__":
    main()
