"""Write the synthetic months to data/, or check that what is on disk is what the generator makes.

    uv run python scripts/make_dataset.py           # write
    uv run python scripts/make_dataset.py --check   # verify, change nothing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recon import synth  # noqa: E402


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for ym in synth.SPECS:
        month = synth.build_month(ym)
        if check:
            for rel, text in synth.render(month).items():
                path = synth.ROOT / rel
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    stale.append(rel)
        else:
            synth.write(month)
        bank = len(month.bank_a) + len(month.bank_d)
        print(f"{ym}: {bank} bank lines, {len(month.gl)} ledger entries, "
              f"{len(month.prior)} carried-forward checks, {len(month.key['groups'])} answer-key groups")
    if stale:
        print("Out of date:", *stale, sep="\n  ")
        return 1
    print("Files match the generator." if check else "Written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
