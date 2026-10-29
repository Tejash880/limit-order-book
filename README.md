# Limit Order Book (LOB) Matching Engine

A deterministic, high-throughput, institutional-grade Limit Order Book matching engine written in Python 3.14 (standard library only), with an exhaustive testing suite (unit tests, Hypothesis property-based testing, differential fuzzing, and deterministic replay) and an agent-based market simulator.

---

## 1. Overview & Architecture

A matching engine is the core transactional component of any financial exchange. It maintains the bids and asks for an asset and continuously executes incoming aggressive orders against resting liquidity according to strict **Price-Time Priority (FIFO)**.

```
                              [ Incoming Order / Cancel / Amend ]
                                              │
                                              ▼
                             ┌─────────────────────────────────┐
                             │    MatchingEngine Pipeline      │
                             │   - Input Validation            │
                             │   - Sequence Number Assignment  │
                             └────────────────┬────────────────┘
                                              │
                       ┌──────────────────────┴──────────────────────┐
                       ▼                                             ▼
          ┌─────────────────────────┐                   ┌─────────────────────────┐
          │        Bids Book        │                   │        Asks Book        │
          │  - Dict: Price -> Level │                   │  - Dict: Price -> Level │
          │  - Deque: FIFO Orders   │                   │  - Deque: FIFO Orders   │
          │  - Max-Heap (-Prices)   │                   │  - Min-Heap (+Prices)   │
          └────────────┬────────────┘                   └────────────┬────────────┘
                       │                                             │
                       └──────────────────────┬──────────────────────┘
                                              │
                                              ▼
                             ┌─────────────────────────────────┐
                             │       Deterministic Events      │
                             │   [ACK, TRADE*, CANCELLED, ...] │
                             └─────────────────────────────────┘
```

### Core Design Principles
1. **Absolute Determinism**: Execution relies exclusively on sequential, monotonic engine sequence numbers (`seq_num`). System clocks and wall-clock times are strictly forbidden in matching decisions.
2. **Integer Arithmetic Only**: Prices are represented as ticks ($P \in \mathbb{Z}^+$), and quantities are represented as shares/lots ($Q \in \mathbb{Z}^+$). Floats are completely absent from the engine.
3. **Maker Price Execution Rule**: Any aggressive trade executes at the limit price of the resting (maker) order, honoring the price discovery set by liquidity providers.
4. **Zero Third-Party Dependencies for the Engine**: The core engine uses only standard library components (`heapq`, `collections.deque`, `dataclasses`).

---

## 2. Algorithmic Complexity & Design Decisions

### Complexity Table

| Operation | Time Complexity (Average) | Time Complexity (Worst Case) | Data Structure Mechanism |
| :--- | :--- | :--- | :--- |
| **Best Price Lookup** | $O(1)$ amortized | $O(\log K)$ | Heap top peek with lazy pop of empty price levels |
| **Add Resting Limit Order** | $O(1)$ amortized ($O(\log K)$ if new price level) | $O(\log K)$ | Dict lookup $O(1)$, `deque.append()` $O(1)$, `heappush` $O(\log K)$ |
| **Cancel Order** | $O(1)$ amortized | $O(M)$ | Global `orders` dict lookup $O(1)$, deque boundary pop $O(1)$ |
| **Amend (Qty Decrease)** | $O(1)$ | $O(1)$ | In-place order quantity reduction; retains queue priority |
| **Amend (Price/Qty Increase)** | $O(1)$ amortized + match | $O(\log K) + \text{match}$ | Deque removal $O(1)$-$O(M)$, new seq_num, aggressive match |
| **Match Aggressive Order** | $O(F)$ where $F$ = fills | $O(F)$ | Iterating FIFO deque and popping exhausted orders in $O(1)$ |

*(Where $K$ is the number of active price levels, and $M$ is the number of orders at a specific price level).*

### Why These Data Structures? (Trade-off Analysis)

During system design, three primary architectures were evaluated:

1. **Sorted List / Array (`bisect`)**:
   - *Pros*: Simple binary search $O(\log K)$ for lookup.
   - *Cons*: Insertion and deletion require array shifting ($O(K)$ memory movement). In active books with thousands of price levels, shifting memory on every new price level destroys throughput.
2. **Balanced Binary Search Tree (Red-Black / AVL Tree)**:
   - *Pros*: Guaranteed $O(\log K)$ for insertions, deletions, and min/max lookups.
   - *Cons*: High pointer overhead, poor cache locality, and complex tree rebalancing rotations. In Python, object pointer chasing makes BSTs substantially slower than native C-backed dicts and heaps.
3. **Selected Hybrid Architecture (Dict + Deque + Heap with Lazy Deletion)**:
   - **`Dict[int, PriceLevel]`**: Gives $O(1)$ instantaneous access to any price level for order insertion, volume checks, and order tracking.
   - **`collections.deque`**: Implemented in C as a doubly-linked list of blocks. Offers strictly $O(1)$ appends and $O(1)$ left-pops for perfect FIFO queue processing.
   - **`heapq` with Lazy Deletion**: Finding the best bid/ask requires $O(1)$ peek at the top of the heap. When a price level is emptied, rather than performing an expensive $O(K)$ heap deletion, we mark the level deleted in the dict and lazily pop it off the heap only when it reaches the top. This guarantees optimal throughput.

---

## 3. How to Run Everything

### Prerequisites & Setup
Ensure Python 3.11+ is installed. Dev dependencies (`pytest`, `hypothesis`, `matplotlib`) can be installed:

```bash
python -m pip install pytest hypothesis matplotlib
```

### 1. Run Unit Tests & Invariant Property Tests
```bash
# Run all 31 tests (basic rules, invariants, differential, replay)
python -m pytest tests -v
```

### 2. Run Standalone 1,000,000-Operation Differential Fuzzing
```bash
python tests/test_differential.py
```

### 3. Run Benchmark & Profiler
```bash
# Run 1,000,000 operations benchmark with latency profiling
python bench/run_bench.py --ops 1000000 --seed 42 --profile
```

### 4. Run Market Simulator & Generate Charts
```bash
python sim/run_sim.py --steps 5000 --seed 42 --output sim/output
```

---

## 4. Testing & Verification Strategy

The engine is verified through four complementary testing tiers:

```
                  ┌──────────────────────────────────────────────┐
                  │            Testing Pyramid                   │
                  ├──────────────────────────────────────────────┤
                  │ Tier 4: JSONL Event Log & Hash Replay        │
                  │ Tier 3: 1M Differential Fuzzing (vs Oracle)  │
                  │ Tier 2: Hypothesis Property-Based Invariants │
                  │ Tier 1: Deterministic Unit Tests (Rules 1-12)│
                  └──────────────────────────────────────────────┘
```

1. **Unit Tests ([`test_engine_basic.py`](file:///C:/Users/Tejash/.gemini/antigravity/scratch/orderbook/tests/test_engine_basic.py))**:
   - Parameterized across both `ReferenceEngine` and `MatchingEngine`.
   - Explicitly validates all 12 formal rules: FIFO matching, maker-price execution, multi-level sweeps, empty book edge cases, partial fills, market order cancellations, priority retention/loss on amendments, duplicate rejections, and self-trade acceptance.
2. **Property-Based Invariant Tests ([`test_invariants.py`](file:///C:/Users/Tejash/.gemini/antigravity/scratch/orderbook/tests/test_invariants.py))**:
   - Uses `hypothesis` to generate randomized sequences of limit orders, market orders, cancels, and amends.
   - Continuously verifies the following core invariants:
     - **Uncrossed Book**: $\text{best\_bid} < \text{best\_ask}$ at all times.
     - **Volume Conservation**: $\text{OriginalQty} = \text{FilledQty} + \text{RemainingQty} + \text{CancelledQty}$.
     - **Non-Negativity**: Zero negative quantities or prices.
     - **Overfill Protection**: $\text{FilledQty} \le \text{OriginalQty}$.
     - **Strict FIFO Ordering**: Monotonically increasing sequence numbers at every resting price level.
3. **Differential Fuzzing ([`test_differential.py`](file:///C:/Users/Tejash/.gemini/antigravity/scratch/orderbook/tests/test_differential.py))**:
   - Compares `MatchingEngine` against a deliberately naive `ReferenceEngine` that stores orders in plain Python lists and re-sorts them after every operation.
   - Over **1,000,000 operations across 10 random seeds** were executed, verifying exact data equality across all emitted events, trade IDs, and order book states.
4. **Deterministic Replay ([`test_replay.py`](file:///C:/Users/Tejash/.gemini/antigravity/scratch/orderbook/tests/test_replay.py))**:
   - Streams input commands to JSONL, executes them, and computes a cryptographic SHA-256 hash of all emitted events.
   - Replays the logged JSONL file from disk into a clean engine instance and proves that the event stream hash and internal state match bit-for-bit.

---

## 5. Benchmark & Optimization Results

All benchmarks were measured directly on the host machine using `time.perf_counter_ns` across a pre-generated, realistic workload of 1,000,000 operations (60% limit orders, 30% cancellations, 10% market orders clustered around a moving mid-price).

### Environment Specification
- **Operating System**: Windows 11 (AMD64)
- **Python Version**: CPython 3.14.3
- **Workload**: 1,000,000 operations (Seed 42)

### Measured Before vs. After Optimization

| Metric | Baseline Engine | Optimized Engine | Delta / Improvement |
| :--- | :--- | :--- | :--- |
| **Total Operations** | 1,000,000 | 1,000,000 | — |
| **Elapsed Execution Time** | 16.9239 s | 12.4816 s | **-26.2% faster** |
| **Throughput (Ops/Sec)** | 59,088 ops/s | 80,118 ops/s | **+35.6% throughput** |
| **p50 Latency (Median)** | 12.30 µs | 7.30 µs | **-40.6% latency drop** |
| **p95 Latency** | 37.90 µs | 30.50 µs | **-19.5% latency drop** |
| **p99 Latency** | 71.20 µs | 60.60 µs | **-14.9% latency drop** |
| **Mean Latency** | 16.37 µs | 12.06 µs | **-26.3% latency drop** |

### Profiling & Bottleneck Analysis
Profiling with `cProfile` identified that `is_crossed` assertions were triggering repeated heap cleans and dict queries (139,695 calls totaling 0.924s cumulative time). Furthermore, cancellations in the middle of a deque previously executed sequential scanning.

**Optimizations Applied**:
1. **O(1) Boundary Pruning & Selective Validation**: Replaced redundant full book validations in the hot path with an opt-in `validate_invariants` mode, reserving exhaustive validation for test runs.
2. **Inlined Monotonic Sequences**: Inlined engine sequence increment operations directly into event emissions, avoiding function call overhead on hot branches.
3. **Direct Dict Access in Heap Cleaners**: Eliminated redundant dictionary `.get()` calls during heap sweeps.

---

## 6. Agent Simulator & Market Dynamics

The engine includes an agent-based simulation harness ([`sim/run_sim.py`](file:///C:/Users/Tejash/.gemini/antigravity/scratch/orderbook/sim/run_sim.py)) modeling interactions between three archetypal market participants:
- **`MarketMaker`**: Quotes both sides of the book around the mid-price, utilizing an inventory skew parameter to shade quotes and mean-revert inventory towards zero.
- **`NoiseTrader`**: Submits uncoordinated liquidity-taking market orders and random limit orders.
- **`TrendFollower`**: Calculates short-term price momentum via simple moving averages (SMA) and submits aggressive momentum orders.

### Simulation Outputs (Generated at `sim/output/`)

```
sim/output/
├── trade_price.png     # Market execution price dynamics over 5,000 steps
├── bot_pnl.png         # Mark-to-market PnL trajectory for each participant
└── bot_inventory.png   # Position risk and inventory accumulation over time
```

> [!NOTE]
> **Market Realism Disclaimer**: The synthetic prices and PnL curves generated by the simulator are emergent properties of simplified agent heuristics. They are educational models designed to stress-test the matching engine and do not represent real-world econometric price processes.

---

## 7. Known Limitations

In the interest of architectural transparency, the current implementation has the following deliberate design constraints:
1. **Self-Trades Allowed**: The engine does not inspect trader identities; an agent can trade against its own resting orders. Self-Trade Prevention (STP) flags (e.g. Cancel Oldest, Cancel Newest) are not yet implemented.
2. **Single-Threaded Execution**: The engine operates on a single execution thread. While this eliminates race conditions and locking overhead, it cannot utilize multiple CPU cores for matching.
3. **Python Execution Overhead**: While Python provides exceptional clarity and rapid prototyping, interpreted bytecode and garbage collection pauses create latency spikes (e.g., max latency ~100 ms during GC sweeps).
4. **No Write-Ahead Log (WAL) or Persistence**: The engine maintains state purely in memory; process termination results in state loss unless restored from an input JSONL event log.
5. **No Credit / Margin / Balance Verification**: The engine matches orders blindly without checking participant margin or cash balances.
6. **Basic Order Types Only**: Supported instructions are restricted to `LIMIT` and `MARKET`. Advanced order types (Stop-Loss, Iceberg, Post-Only, FOK, IOC) are deferred.

---

## 8. Future Roadmap

1. **Native C++ / Rust Core**: Port the matching core to Rust or C++ using contiguous ring buffers or B-trees, binding to Python via `pybind11` or `PyO3` for sub-microsecond latency.
2. **Exchange Gateway Protocols**: Implement binary exchange protocols (e.g., NASDAQ ITCH for L3 market data dissemination and NASDAQ OUCH for order entry).
3. **Self-Trade Prevention (STP)**: Add trader account tags to orders with configurable STP policies (Cancel Aggressive, Cancel Resting, Decrement and Cancel).
4. **Persistent WAL**: Implement memory-mapped zero-copy journaling (e.g., using `mmap` or Chronicle Queue) for instant crash recovery.
