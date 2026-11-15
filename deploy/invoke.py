"""Invoke the deployed agent with one or more texts in the SAME AgentCore session.

    python deploy/invoke.py --arn <agent_runtime_arn> examples/papers/butterfly_migration_abstract.txt \
                                                       examples/papers/butterfly_migration_abstract_l.txt

The runtime ARN is printed by `terraform output agent_runtime_arn`. It can also come from the
AGENT_RUNTIME_ARN environment variable. Reuse --session-id to keep extending the same graph.
"""
import argparse
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, help="Text files sent in order, one invocation each")
    parser.add_argument("--arn", default=os.getenv("AGENT_RUNTIME_ARN"), help="AgentCore Runtime ARN")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    # runtimeSessionId must be at least 33 characters.
    parser.add_argument("--session-id", default=f"kg-session-{uuid.uuid4().hex}")
    args = parser.parse_args()
    if not args.arn:
        parser.error("--arn is required (or set AGENT_RUNTIME_ARN)")
    if len(args.session_id) < 33:
        parser.error("--session-id must be at least 33 characters")
    return args


def main() -> None:
    args = parse_args()
    # A run takes 5-20 s plus the session cold start; the default 60 s read timeout is too tight.
    client = boto3.client("bedrock-agentcore", region_name=args.region, config=Config(read_timeout=300))
    print(f"session: {args.session_id}")

    result = {}
    for path in args.files:
        start = time.perf_counter()
        response = client.invoke_agent_runtime(
            agentRuntimeArn=args.arn,
            runtimeSessionId=args.session_id,
            payload=json.dumps({"prompt": path.read_text(encoding="utf-8")}),
            qualifier="DEFAULT",
        )
        result = json.loads(response["response"].read())
        if "error" in result:
            raise SystemExit(f"{path.name}: {result['error']}")
        graph = result["knowledge_graph"]
        print(f"{path.name}: {time.perf_counter() - start:.1f}s | texts={result['texts_processed']} "
              f"relations={len(graph['relations'])} unlinked={len(result['unlinked_entities'])} "
              f"failed_chunks={result['failed_chunks']}")

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"kg_agentcore_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
