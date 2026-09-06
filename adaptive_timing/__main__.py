import argparse
from pathlib import Path
from .experiment import experiment, write_report, write_viewer


def positive(text):
    value = int(text)
    if not 1 <= value <= 10000:
        raise argparse.ArgumentTypeError("events must be between 1 and 10000")
    return value


def main():
    parser = argparse.ArgumentParser(description="Inspect adaptive timing on generated workloads.")
    parser.add_argument("command", choices=("demo", "benchmark"))
    parser.add_argument("--events", type=positive, default=240)
    parser.add_argument("--output", type=Path, default=Path("reports"))
    args = parser.parse_args()
    report = experiment(args.events)
    write_report(report, args.output)
    if args.command == "demo":
        write_viewer(report, args.output / "demo.html")
        print(f"Open {args.output / 'demo.html'} in a browser.")
    print(f"{len(report['cases'])} paired cases; evidence: {args.output / 'results.md'}")


if __name__ == "__main__":
    main()
