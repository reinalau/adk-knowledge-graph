"""Run the long-document workflow on one or more .txt files in the SAME session.

    python main_docs.py paper.txt                  # one text, one graph
    python main_docs.py part1.txt part2.txt        # incremental: part2 extends the graph of part1
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from kg_pipeline.runner import run_session
from kg_pipeline_docs.agent import root_agent

ROOT = Path(__file__).parent


async def main(paths: list[Path]) -> None:
    texts = [p.read_text(encoding="utf-8") for p in paths]
    start = time.perf_counter()
    results = await run_session(texts, agent=root_agent)
    elapsed = time.perf_counter() - start

    per_text = []
    for path, text, result in zip(paths, texts, results):
        state = result.state
        graph = state.get("kg_accumulated", {"entities": [], "relations": []})
        row = {
            "source": path.name,
            "words": len(text.split()),
            "chunks": len(state.get("doc_chunks", [])),
            "skipped_chunks": state.get("skipped_chunks", 0),
            "failed_chunks": state.get("failed_chunks", []),
            "revisions": result.revisions,
            "accumulated_relations": len(graph["relations"]),
        }
        per_text.append(row)
        print(json.dumps(row, ensure_ascii=False))
    print(f"seconds: {elapsed:.1f}")

    last = results[-1]
    if last.knowledge_graph is None:
        print("No knowledge graph produced.")
        return

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"kg_docs_{paths[0].stem}_{datetime.now():%Y%m%d_%H%M%S}.json"
    payload = {
        "texts": per_text,
        "knowledge_graph": last.knowledge_graph.model_dump(),
        "unlinked_entities": last.state.get("unlinked_entities", []),
        "applied_aliases": last.state.get("applied_aliases", {}),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"entities={len(last.knowledge_graph.entities)} relations={len(last.knowledge_graph.relations)} "
          f"unlinked={len(payload['unlinked_entities'])}")
    print(f"saved: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python main_docs.py text1.txt [text2.txt ...]")
    asyncio.run(main([Path(p) for p in sys.argv[1:]]))
