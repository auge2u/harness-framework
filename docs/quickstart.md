# Quick Start

## Installation

```bash
pip install harness-framework
```

Or from source:

```bash
git clone https://github.com/your-org/harness-framework.git
cd harness-framework
pip install -e ".[dev]"
```

## Your First Harness

```python
from harness import HarnessConfig, Surface, SurfaceType
from harness.runners import SingleRunner
from harness.agent_backend import MockBackend

# Define what you're testing
config = HarnessConfig(
    version="1.0.0",
    name="hello-world",
    surfaces=[
        Surface(name="api", type=SurfaceType.API),
    ],
    scenarios={...},
    verifiers=[...],
)

# Run it
runner = SingleRunner(config, backend=MockBackend())
results = runner.run_all()

for scenario, (verdict, score, latency, cost, raw) in results.items():
    print(f"{scenario}: {verdict.value}")
```

## Next Steps

- Read the [Architecture Guide](architecture.md)
- Explore the [18 Declared Surfaces](surfaces.md)
- Learn about [Knowledge Graphs](knowledge_graph.md)
- Try [Swarm Orchestration](swarm.md)
- Understand [Lifecycle Hooks](lifecycle.md)
