"""Run the pipeline on a text file: python main.py [path]  (default: examples/sample_input.txt)"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from kg_pipeline.runner import run_pipeline

ROOT = Path(__file__).parent


async def main(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    result = await run_pipeline(text)

    print("execution_order:", " -> ".join(result.execution_order))
    print("revisions:", result.revisions)
    if result.knowledge_graph is None:
        print("No knowledge graph produced.")
        return

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"kg_{datetime.now():%Y%m%d_%H%M%S}.json"
    unlinked = result.state.get("unlinked_entities", [])
    payload = {"knowledge_graph": result.knowledge_graph.model_dump(), "unlinked_entities": unlinked}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"entities={len(result.knowledge_graph.entities)} relations={len(result.knowledge_graph.relations)} "
          f"unlinked={len(unlinked)}")
    print(f"saved: {out}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "sample_input.txt"))
