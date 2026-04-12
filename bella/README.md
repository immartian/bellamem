# BELLA — Bayesian Epistemic Logical Lattice for Accumulation

A formal calculus for constructing belief structures from evidence
streams. Six rules, scale-free, self-referential.

These documents define the theory. The working implementation lives
in [`bellamem/`](../bellamem/) — a Python package that applies BELLA
to persistent memory for AI agents. See
[THEORY.md](../THEORY.md) for how bellamem maps these definitions
to code.

## Documents

| File | What |
|---|---|
| [SPEC.md](SPEC.md) | The six rules, invariants, formal definitions |
| [VISION.md](VISION.md) | Theoretical grounding — Jaynes, Gödel, consciousness, self-reference |
| [EXAMPLES.md](EXAMPLES.md) | Domain-agnostic case studies (H. pylori, continental drift, …) |
| [MEMORY.md](MEMORY.md) | How BELLA maps to LLM agent memory architecture |

## Origin

BELLA was developed as part of the
[Recursive Emergence](https://github.com/Recursive-Emergence/RE)
research programme. The formal calculus is domain-agnostic; bellamem
is its first shipped application.
