"""Unified CLI adapters for offline architecture and lifecycle reviews."""
from atlaspm.framework.trading_lifecycle import Receipt, Step

COMMANDS={"architecture","lifecycle-review"}


def register_parsers(sub):
    sub.add_parser("architecture",help="Inspect offline foundation contracts")
    p=sub.add_parser("lifecycle-review",help="Local declared lifecycle journal (no trading)")
    p.add_argument("action",choices=("create","inspect","append"))
    p.add_argument("--review-id",required=True)
    p.add_argument("--step",choices=[s.value for s in Step])
    p.add_argument("--evidence-ref")
    p.add_argument("--outcome",choices=("reviewed","blocked"))
    p.add_argument("--note")
    p.add_argument("--expected-revision")


def run(args):
    if args.command=="architecture":
        from atlaspm.framework.architecture import inspect
        return inspect()
    from atlaspm.framework import lifecycle_journal as journal
    fields=(args.step,args.evidence_ref,args.outcome,args.note,args.expected_revision)
    if args.action!="append":
        if any(v is not None for v in fields):
            raise ValueError("Receipt options require append")
        return journal.create(args.review_id) if args.action=="create" else journal.inspect(args.review_id)
    if any(v is None for v in (args.step,args.evidence_ref,args.outcome,args.expected_revision)):
        raise ValueError("Append requires step, evidence-ref, outcome and expected-revision")
    receipt=Receipt(Step(args.step),args.evidence_ref,args.outcome,args.note or "")
    return journal.append(args.review_id,receipt,expected_revision=args.expected_revision)
