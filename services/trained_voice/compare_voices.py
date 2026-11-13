"""Private paired auditions. Never activates a config or infers listening quality."""

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from .evaluation_metrics import wav_metrics

LISTENING_CHECKS = (
    "identity",
    "pronunciation",
    "naturalness",
    "no_echo",
    "no_distortion",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_status(report, reviews):
    """Require review of these exact waveforms; diagnostics cannot approve audio."""
    reasons = []
    for row in report["samples"]:
        key = row["id"]
        acoustic = row["acoustic"]
        if (
            acoustic["clippedSampleFraction"] > 0.005
            or acoustic["quietSampleFraction"] > 0.95
        ):
            reasons.append(f"{key}: clipping or near-silence")
        review = reviews.get(key, {})
        if review.get("sha256") != row["sha256"]:
            reasons.append(f"{key}: missing or stale listening review")
        elif any(review.get(check) is not True for check in LISTENING_CHECKS):
            reasons.append(f"{key}: listening checks incomplete or rejected")
        if (
            row.get("variant") == "candidate"
            and review.get("notWorseThanBaseline") is not True
        ):
            reasons.append(f"{key}: paired comparison incomplete or regressed")
    if not report["samples"]:
        reasons.append("No evaluated samples")
    # This is a review result, not an authorization to deploy.
    return {
        "listeningPassed": not reasons,
        "reasons": reasons,
        "automaticPromotion": False,
    }


def run(configs, prompts, output):
    from .reference_voice import synthesize

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    report = {
        "schemaVersion": 1,
        "samples": [],
        "configs": {},
        "limitation": "Clipping/silence/timing are diagnostics. Echo, accent, pronunciation, identity and naturalness require listening. No automatic activation.",
    }
    for name, config in configs.items():
        config = Path(config).resolve()
        cfg = json.loads(config.read_text())
        assets = [config.parent / cfg["profile"]]
        if cfg.get("adapter"):
            assets.append(config.parent / cfg["adapter"])
        report["configs"][name] = {
            "path": str(config),
            "sha256": digest(config),
            "revision": cfg.get("revision"),
            "assets": {str(p): digest(p) for p in assets},
        }
        for index, prompt in enumerate(prompts):
            ident = f"{name}-{index:02d}"
            path = output / f"{ident}.wav"
            args = SimpleNamespace(
                config=str(config), output=str(path), delivery="{}", stream_dir=None
            )
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                synthesize(args, prompt["text"])
            metrics = None
            for line in captured.getvalue().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and "generationSeconds" in event:
                    metrics = event
            if metrics is None:
                raise ValueError("Synthesis did not return timing metrics")
            report["samples"].append(
                {
                    "id": ident,
                    "variant": name,
                    **prompt,
                    "audio": path.name,
                    "sha256": digest(path),
                    "metrics": metrics,
                    "generationSeconds": metrics["generationSeconds"],
                    "acoustic": wav_metrics(path),
                }
            )
            (output / "comparison.json").write_text(json.dumps(report, indent=2))
            print(json.dumps({"id": ident, "metrics": metrics}), flush=True)
    reviews = {
        r["id"]: {
            "sha256": r["sha256"],
            **{c: None for c in LISTENING_CHECKS},
            **({"notWorseThanBaseline": None} if r["variant"] == "candidate" else {}),
            "notes": "",
        }
        for r in report["samples"]
    }
    (output / "listening-review.json").write_text(json.dumps(reviews, indent=2))
    (output / "samples.json").write_text(json.dumps(report["samples"], indent=2))
    (output / "review-status.json").write_text(
        json.dumps(review_status(report, reviews), indent=2)
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--prompts")
    parser.add_argument("--output", required=True)
    parser.add_argument("--check-review", action="store_true")
    args = parser.parse_args()
    if args.check_review:
        out = Path(args.output)
        report = json.loads((out / "comparison.json").read_text())
        for config in report["configs"].values():
            for path, expected in {
                config["path"]: config["sha256"],
                **config["assets"],
            }.items():
                if digest(path) != expected:
                    parser.error(
                        "Evaluated config or voice asset changed after generation"
                    )
        # Reject altered audio even if an old review has a matching report hash.
        for row in report["samples"]:
            if digest(out / row["audio"]) != row["sha256"]:
                parser.error("Evaluated waveform changed after generation")
        status = review_status(
            report, json.loads((out / "listening-review.json").read_text())
        )
        (out / "review-status.json").write_text(json.dumps(status, indent=2))
        print(json.dumps(status))
        return
    if not args.baseline or not args.prompts:
        parser.error("--baseline and --prompts are required for generation")
    prompts = json.loads(Path(args.prompts).read_text())
    if (
        not isinstance(prompts, list)
        or not prompts
        or any(
            not isinstance(p, dict)
            or not isinstance(p.get("text"), str)
            or not p["text"].strip()
            for p in prompts
        )
    ):
        parser.error("Prompts must be a nonempty list of text records")
    configs = {"baseline": args.baseline}
    if args.candidate:
        configs["candidate"] = args.candidate
    run(configs, prompts, args.output)


if __name__ == "__main__":
    main()
