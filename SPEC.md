# Limit Order Book (LOB) Specification

## 1. System Overview & Objectives
This document specifies the exact behavioral contract, data models, event semantics, and invariant guarantees for a deterministic, single-threaded Limit Order Book (LOB) matching engine.

The core design objectives are:
1. **Absolute Determinism**: Execution depends solely on the sequence of incoming commands and engine-assigned sequence numbers. No system clock, timestamps, or external entropy influence state transitions.
2. **Predictable Priority**: Strict Price-Time Priority (FIFO within the same price level).
3. **Auditability & Traceability**: Every command returns a deterministic sequence of immutable event records. The entire state of the engine can be reproduced from the input sequence or verified via event logs.
4. **Zero Float Math**: All prices (in ticks) and quantities (in lots/shares) are strictly positive 64-bit integers.

---

## 2. Terminology & Core Concepts

- **Tick**: The minimum price increment. All prices are represented as positive integers ($P \in \mathbb{Z}^+$).
- **Lot / Share**: The atomic unit of trade quantity. All quantities are represented as positive integers ($Q \in \mathbb{Z}^+$).
- **Side**:
  - `BUY` (Bid): An intention to buy at or below a specified price.
  - `SELL` (Ask / Offer): An intention to sell at or above a specified price.
- **Order Types**:
  - `LIMIT`: An order to buy or sell a specified quantity at a specified price or better. Matches immediately if marketable; any unfilled remainder rests on the book.
  - `MARKET`: An order to buy or sell immediately at the best available prices on the opposite side of the book. Never rests. Any unfilled remainder is immediately cancelled.
- **Resting (Maker) Order**: An order already sitting in the order book awaiting execution.
- **Aggressing (Taker) Order**: An incoming order that matches against resting orders.
- **Book Crossing**: A condition where `best_bid >= best_ask`. The matching engine must maintain the strict invariant `best_bid < best_ask` after every operation completes.

---

## 3. Data Types & Precision

1. **Identifiers**:
   - `order_id`: String or integer client-provided identifier, unique across all active and historical orders.
   - `seq_num`: 64-bit unsigned monotonically increasing integer assigned sequentially by the engine to every accepted order and event.
   - `trade_id`: 64-bit unsigned monotonically increasing integer assigned sequentially to each execution.
2. **Numerical Fields**:
   - `price`: Integer ticks ($P \ge 1$). For `MARKET` orders, price is not specified (`None`).
   - `quantity`: Integer ($Q \ge 1$). Represents total or remaining quantity depending on context.

---

## 4. Formal Rules of the Matching Engine

### Rule 1: Order Types and Sides
- Supported Sides: `BUY`, `SELL`.
- Supported Order Types: `LIMIT`, `MARKET`.

### Rule 2: Price-Time Priority (FIFO)
- **Primary Priority (Price)**:
  - For `BUY` orders: Higher price has strict priority over lower price.
  - For `SELL` orders: Lower price has strict priority over higher price.
- **Secondary Priority (Time)**:
  - Within the exact same price level, earlier orders have strict priority over later orders.
  - "Earlier" is determined strictly by the engine-assigned `seq_num` upon arrival (or upon re-entry/priority loss), never by wall-clock time.

### Rule 3: Execution Price Determination
- Trade execution price is **always determined by the resting order's limit price**.
- An incoming aggressive order accepts the price established by the maker already resting in the book.

### Rule 4: Limit Order Execution & Resting
- An incoming `LIMIT BUY` with price $P_{in}$ matches against resting `SELL` orders with price $P_{rest} \le P_{in}$ in ascending order of price, and FIFO within each price.
- An incoming `LIMIT SELL` with price $P_{in}$ matches against resting `BUY` orders with price $P_{rest} \ge P_{in}$ in descending order of price, and FIFO within each price.
- If the incoming limit order is not fully filled after exhausting all crossing resting orders, the remaining unfilled quantity ($Q_{rem} > 0$) rests in the book at price $P_{in}$ with its assigned `seq_num`.

### Rule 5: Market Order Execution & Cancellation
- An incoming `MARKET BUY` matches against resting `SELL` orders starting from the lowest ask upwards.
- An incoming `MARKET SELL` matches against resting `BUY` orders starting from the highest bid downwards.
- A market order matches until fully filled or until the opposite side of the book is completely empty.
- Any unfilled remainder is immediately cancelled. **A market order never rests in the book.**

### Rule 6: Partial Fills & Multi-Level Sweeps
- Partial fills are fully supported for both resting and incoming orders.
- A single incoming order may generate zero, one, or multiple trades across multiple price levels and multiple resting orders until filled or exhausted.
- Each fill decrements the remaining quantity of the resting order and the incoming order.
- A resting order whose remaining quantity reaches 0 is removed from the book.

### Rule 7: Order Cancellation (`CANCEL`)
- A cancel request targets an existing order by `order_id`.
- If the order is active and resting:
  - The remaining quantity is removed from the book.
  - An event of type `CANCELLED` is returned.
- If the `order_id` is unknown, already fully filled, or already cancelled:
  - A `REJECT` event is returned with reason `ORDER_NOT_FOUND`.
  - The engine does not crash or raise an unhandled exception.

### Rule 8: Order Amendment (`AMEND`)
- A client requests an amendment with parameters `(order_id, new_price, new_qty)` where `new_qty` represents the new *remaining quantity*.
- Validations:
  - If `order_id` is not active: Reject with `ORDER_NOT_FOUND`.
  - If `new_price <= 0` or `new_qty <= 0`: Reject with `INVALID_AMEND_PARAMS`.
- Priority Rules:
  - **Case A (Priority Retained)**: If `new_price == old_price` and `new_qty < old_qty`:
    - The resting order's remaining quantity is updated in place.
    - The price level total volume is updated.
    - The order **retains its original time priority (`seq_num`)**.
    - An `AMENDED` event is emitted.
  - **Case B (Priority Lost)**: If `new_price != old_price` or `new_qty > old_qty`:
    - The amendment is processed equivalent to a cancellation followed by a new order entry:
    - The old order is removed from its resting price level.
    - The order is assigned a brand-new engine `seq_num`.
    - If `new_price` crosses the opposite side of the book, it matches aggressively according to Rule 4.
    - Any unfilled remainder rests at `new_price` with the new `seq_num`.
    - An `AMENDED` event is emitted (along with any resulting `TRADE` events and/or resting placement).
  - **Case C (No-Op)**: If `new_price == old_price` and `new_qty == old_qty`:
    - An `AMENDED` event is emitted with no state change.

### Rule 9: Validation & Rejection Semantics
Every request is validated before engine state modification. Invalid requests return a single `REJECT` event with a clear error reason:
1. `DUPLICATE_ORDER_ID`: An order with this ID has already been submitted (active or completed).
2. `INVALID_QUANTITY`: Order quantity $\le 0$ or non-integer.
3. `INVALID_PRICE`: `LIMIT` order price $\le 0$ or non-integer.
4. `INVALID_SIDE`: Side is not `BUY` or `SELL`.
5. `INVALID_ORDER_TYPE`: Order type is not `LIMIT` or `MARKET`.
6. `ORDER_NOT_FOUND`: Target `order_id` does not exist or is no longer active on cancel/amend.
7. `INVALID_AMEND_PARAMS`: `new_qty <= 0` or `new_price <= 0`.

### Rule 10: Self-Trades
- Self-crossing / self-matching (an order from a trader matching against another order from the same trader) is **not prevented**.
- The engine matches strictly based on price-time priority without trader identity checks.
- *Documentation Note*: Self-Trade Prevention (STP) mechanisms (e.g., Cancel Oldest, Cancel Newest, Decrement and Cancel) are reserved for future work.

### Rule 11: Crossed Book Invariant
- At the start and completion of every engine operation, the order book must satisfy:
  $$\text{best\_bid} < \text{best\_ask}$$
- If either the bid side or ask side (or both) is empty, the condition is vacuously satisfied.
- An incoming order that would cause a crossed book must execute immediately against resting orders until it is either filled or no longer crosses.

### Rule 12: Deterministic Event Output Ordering
Every operation submitted to the matching engine returns an ordered list of events `List[Event]`:

1. **New Order Accepted (No Immediate Match)**:
   - `[ACK]`
2. **New Order (Partial or Full Execution)**:
   - For every fill: `TRADE` (in chronological matching sequence).
   - If the aggressive order had remaining quantity that rests: `ACK` is emitted (indicating remainder entered the book).
   - If the aggressive order was a `MARKET` order with unfilled remainder: `CANCELLED` is emitted for the remaining quantity.
   - If the aggressive order is fully filled: A concluding `ACK` or full-fill indication is captured. *(Convention: `ACK` is emitted first when an order is accepted into the engine, followed by `TRADE` events, followed by resting `ACK` or `CANCELLED` for remaining market order qty. See Section 5 for exact event ordering specification).*
3. **Rejected Request**:
   - `[REJECT]`
4. **Cancelled Order**:
   - `[CANCELLED]`
5. **Amended Order**:
   - If priority preserved: `[AMENDED]`
   - If priority lost and trades generated: `[AMENDED, TRADE, TRADE, ...]`

---

## 5. Event Model Specification

All events inherit from a base `Event` schema:

```python
class Event:
    seq_num: int      # Monotonic engine sequence number
    order_id: str     # Originating order identifier
```

### Event Variants
1. **`AckEvent`**:
   - Indicates that an order has been validated, assigned a sequence number, and accepted.
   - Fields: `seq_num`, `order_id`, `side`, `order_type`, `price`, `quantity`, `remaining_qty`.
2. **`RejectEvent`**:
   - Indicates that a command was invalid or could not be processed.
   - Fields: `seq_num`, `order_id`, `reason`.
3. **`TradeEvent`**:
   - Indicates an execution between an incoming order (taker) and a resting order (maker).
   - Fields: `seq_num`, `trade_id`, `maker_order_id`, `taker_order_id`, `maker_side`, `price`, `quantity`, `maker_remaining_qty`, `taker_remaining_qty`.
4. **`CancelledEvent`**:
   - Indicates that an active order (or market order remainder) was removed from the book.
   - Fields: `seq_num`, `order_id`, `cancelled_qty`, `reason`.
5. **`AmendedEvent`**:
   - Indicates an amendment was applied.
   - Fields: `seq_num`, `order_id`, `old_price`, `new_price`, `old_qty`, `new_qty`, `priority_lost`.

### Exact Event Sequence Convention
To eliminate all ambiguity during differential testing and replay, the engine adheres to this exact sequence:

1. **Incoming Limit Order**:
   - If rejected: `[RejectEvent]`
   - If accepted:
     - `AckEvent` (acknowledging entry into the matching pipeline).
     - Zero or more `TradeEvent` records (one per matched resting fill).
     - If `remaining_qty == 0`: No additional events (order completed).
     - If `remaining_qty > 0`: The remaining portion rests on the book.
2. **Incoming Market Order**:
   - If rejected (e.g. invalid qty): `[RejectEvent]`
   - If accepted:
     - `AckEvent`
     - Zero or more `TradeEvent` records.
     - If `remaining_qty > 0`: `CancelledEvent` for the unfilled remainder.
3. **Cancel Command**:
   - If invalid/not found: `[RejectEvent]`
   - If valid: `[CancelledEvent]`
4. **Amend Command**:
   - If invalid/not found: `[RejectEvent]`
   - If valid (no match): `[AmendedEvent]`
   - If valid (causes matches): `[AmendedEvent, TradeEvent, ...]`

---

## 6. Formal Invariants (Properties for Verification)

The following properties must hold unconditionally after every engine invocation:

1. **Uncrossed Book**:
   $$\forall \text{ state }, \quad (\text{best\_bid} < \text{best\_ask}) \lor (\text{best\_bid} \text{ is None}) \lor (\text{best\_ask} \text{ is None})$$
2. **Conservation of Volume**:
   For every order $i$ submitted:
   $$\text{OriginalQty}_i = \text{FilledQty}_i + \text{RemainingQty}_i + \text{CancelledQty}_i$$
3. **Non-Negativity**:
   $$\forall \text{ resting order } i, \quad \text{RemainingQty}_i > 0, \quad \text{Price}_i > 0$$
   $$\forall \text{ trade } t, \quad \text{Quantity}_t > 0, \quad \text{Price}_t > 0$$
4. **Price Improvement & Maker Price**:
   For every trade $t$ generated by taker $T$ against resting order $M$:
   $$\text{Price}_t = \text{Price}_M$$
   If $T$ is `BUY`: $\text{Price}_t \le \text{Price}_T$ (for limit orders).
   If $T$ is `SELL`: $\text{Price}_t \ge \text{Price}_T$ (for limit orders).
5. **Strict Time Priority**:
   For any two resting orders $A$ and $B$ on the same side with $\text{Price}_A = \text{Price}_B$:
   $$\text{seq\_num}_A < \text{seq\_num}_B \implies A \text{ matches before } B$$
6. **Book Volume Consistency**:
   For every price level $P$:
   $$\text{LevelTotalQty}(P) = \sum_{o \in \text{Orders}(P)} \text{RemainingQty}(o)$$

---

## 7. Ambiguities Identified & Clarifications

1. **Market Orders on an Empty Book**:
   - *Question*: Does a market order on an empty book reject, or does it accept and immediately cancel with 0 trades?
   - *Resolution*: It is accepted (`AckEvent`), executes 0 trades, and emits `CancelledEvent(cancelled_qty=order.qty, reason="UNFILLED_MARKET_ORDER")`. This matches institutional exchange behavior (e.g. IOC/Immediate-Or-Cancel nature of market orders).
2. **Ack Timing for Crossing Orders**:
   - *Question*: Is an incoming marketable order acknowledged before or after execution trades?
   - *Resolution*: It emits `AckEvent` first. This acknowledges receipt and assigns the base sequence number, followed by the execution `TradeEvent` records.
3. **Amend Quantity Definition**:
   - *Question*: Does `new_qty` in `AMEND` represent total order quantity or new remaining quantity?
   - *Resolution*: `new_qty` specifies the *new remaining quantity* to be rested in the book. If an order originally for 100 had 40 filled (60 remaining), amending to `new_qty=50` is a quantity decrease ($50 < 60$), so it retains priority. Amending to `new_qty=70` is an increase ($70 > 60$), so it loses priority.
4. **Order ID Namespace**:
   - *Question*: Can an `order_id` be reused after the original order is fully filled or cancelled?
   - *Resolution*: No. `order_id` must be globally unique across the engine's lifetime. Attempting to reuse an `order_id` returns `RejectEvent(reason="DUPLICATE_ORDER_ID")`.

---

## 8. Summary of Engine Interface

```python
class MatchingEngine:
    def process_order(self, order_id: str, side: Side, order_type: OrderType, quantity: int, price: int | None = None) -> list[Event]: ...
    def cancel_order(self, order_id: str) -> list[Event]: ...
    def amend_order(self, order_id: str, new_price: int, new_qty: int) -> list[Event]: ...
```
