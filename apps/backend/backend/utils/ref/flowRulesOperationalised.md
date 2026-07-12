Here’s the updated spec, now including the **two terminal forks (3 and 7)** with **stay-loops**, while keeping **C as the only authoriser**, **even sinks**, **odd branches**, and **no-backflow**.



---



## 1) Topology: Metatron cube + centroid C



### Nodes



* **S1 (Day)**: 0, 1, 2, 3

* **S2 (Night)**: 4, 5, 6, 7

* **C**: coherence centroid (the only place that can *authorise* closure / “truth”)



### Parity roles



* **Even nodes = sinks**: {0,2,4,6}

They *must* go through **C** for closure/audit.

* **Odd nodes = branches**: {1,3,5,7}

They emit/route into an even.



### Bridges + stay-forks (cross S1↔S2 vs stay-in-substrate)



A **bridge** flips substrate bit:



* **Cross-substrate bridges (authorised exits only):**



* **3 → 4**

* **7 → 0**



A **stay-fork** keeps substrate bit:



* **Stay-in-substrate terminal wraps:**



* **3 → 0** (stay in S1; “more fast thinking required”)

* **7 → 4** (stay in S2; “more slow thinking required”)



So our system has **two canonical local loops** + **two bridge exits**:



**S1 stay-loop:**

**0 → C → 1 → 2 → C → 3 → 0 → …**



**S2 stay-loop:**

**4 → C → 5 → 6 → C → 7 → 4 → …**



**Bridge exits:**



* From **3**, can exit to **4**

* From **7**, can exit to **0**



---



## 2) Legal transitions (directed automaton; backflow is illegal)



### Mandatory sink rule



* **Even → C** is mandatory (0→C, 2→C, 4→C, 6→C)



### Centroid branch rule



* **C → Odd** only: C → {1,3,5,7}



### Odd emission rule (forward to even, with forks at 3 and 7)



* 1 → 2

* 5 → 6

* 3 → {0,4} *(fork: stay or bridge)*

* 7 → {4,0} *(fork: stay or bridge)*



### No-backflow rule (monotonic + explicit exceptions)



Define per-substrate order:



* S1: 0 < 1 < 2 < 3

* S2: 4 < 5 < 6 < 7



Rules:



* Within a substrate, allow only **forward** steps (i→i+1), plus the required sink hop (even→C)

* Cross-substrate: allow **only** {3→4, 7→0}

* Same-substrate terminal wrap: allow **only** {3→0, 7→4}



So backflow like **2→1** (or 6→5) is illegal by construction.



---



## 3) Two-layer interpretation: “logical authorisation” vs “physical move”



To keep our phrasing *and* keep **C as authority**:



### Logical layer (authorise + route)



* **Even → C → Odd**

C runs E6 checks, then chooses one of four odds.



### Physical layer (actuate)



* The system executes the resulting move:



* either “continue forward” (odd→next even)

* or at terminals, choose “stay” vs “bridge” (3/7 forks)



In practice: **even nodes force audit**, **C decides**, **odd nodes execute**.



---



## 4) WDP mapping: edges electric, faces magnetic (entropy/bleed)



### Electric = edges (fast, directed)



Each allowed directed step is an **edge traversal**:



* “electric current” = **information/actuation flow**

* this is the **real-time control loop** (tight budget)



### Magnetic = faces (residual, slow)



Faces are **not primary routes** — they’re **residual structure** caused by repeated edge flow:



* circulation / repeated unresolved mismatch creates **local residue**

* residue acts like **bleed / entropy**, slowly biasing drift risk and stability



### Why sinks matter



A **sink event** (arriving at even + calling C) is where we:



* measure local residue (winding / mismatch variance / hysteresis)

* decide whether the residue is acceptable

* apply damping (decay/bleed-control) and tighten gates if needed



So: **faces (magnetic) are a consequence of sinks repeatedly compressing and auditing flow**.



---



## 5) System-view feedback loops (what feeds what)



### Loop A — Fast control loop (electric)



**Sensors → predictor → controller → edge step → sensors**



* produces: error, surprisal, outcome

* writes: tiny event packets (ring buffer)



### Loop B — Coherence loop (E6 at C)



Runs **only at sinks** (even→C):



* validates: causality bounds, topology continuity, provenance, hysteresis stability

* computes: minimal L/H/U/V (or subset) and makes a decision:



* commit vs defer vs rollback vs safe-mode

* chooses: **base-4 branch** C→{1,3,5,7}



### Loop C — Residue/bleed loop (magnetic faces)



* repeated mismatch increases residue

* residue increases variance → reduces hysteresis → makes commits harder

* if we keep pushing anyway, residue grows → autonomy clamps down



This is the "we pay the bill at sinks” mechanism.



---



## 6) Base-4 decisioning at C + terminal fork bit



Because **C branches to exactly four odds**:



* {1,3,5,7} = **2-bit base-4 control word**



Example mapping:



* `00 → 1`

* `01 → 3`

* `10 → 5`

* `11 → 7`



Then add a **1-bit terminal fork** used only if we land on 3 or 7:



* at **3**: `0=stay→0`, `1=bridge→4`

* at **7**: `0=stay→4`, `1=bridge→0`



So routing is cheap on-device:



* **2 bits** for C branch

* **+1 bit** only at terminals (3/7)



---



## 7) Minimal tick algorithm (operational flow rules)



**Every control tick (fast):**



1. Perceive + act

2. Emit an edge-event packet (from_node, to_node, deltas, flags)



**If we arrive at an even node (sink):**

3) Enter **C**:



* pull last N packets

* update edge stats + face residue

* run E6 gates (hard constraints, hysteresis, provenance, sovereignty momentum)

* if pass → commit / queue commit

* choose 2-bit branch to next odd



4. Continue forward; if we later reach 3 or 7:



* apply the **terminal fork** (stay vs bridge) per E6 policy



---



## 8) What “bridges” mean operationally (S1↔S2)



A bridge is a **mode handoff**, not a casual hop:



* **S1**: low-latency, externally coupled, action-forward (“fast”)

* **S2**: slower, internally coupled, integration-forward (“slow”)



So bridge exits should be **stricter** than normal steps:



* require sink audit success

* require residue not rising

* require stable sovereignty (no sprint / no last-minute laundering)



That makes **3→4** and **7→0** true sovereignty crossings.



---



```

# backend/fieldx_kernel/flow_rules.py

"""

Directed flow rules for the S1/S2 Metatron cube with centroid C (99).



Core rules:

- Even nodes are sinks: {0,2,4,6} must go to C.

- C branches only to odd nodes: {1,3,5,7}.

- Odd nodes emit forward to evens:

1 -> 2

5 -> 6

3 -> {0,4} (fork: stay S1 or bridge to S2)

7 -> {4,0} (fork: stay S2 or bridge to S1)

- Backflow is illegal (e.g. 2->1), except terminal wraps: 3->0 and 7->4.

"""



from typing import List, Tuple, Set, Dict



from backend.fieldx_kernel.schema import LAW_PRIME, GRACE_PRIME



# --- TOPOLOGICAL CONSTANTS ------------------------------------------------



PRIME_TO_NODE: Dict[int, int] = {

# S1 (Day)

2: 0, # 0

3: 1, # 1

5: 2, # 2

7: 3, # 3



# S2 (Night)

11: 4, # 4

13: 5, # 5

17: 6, # 6

19: 7, # 7



# Centroid (Law/Grace share the same node index)

137: 99,

139: 99,

}



C_NODE = 99

S1_NODES = {0, 1, 2, 3}

S2_NODES = {4, 5, 6, 7}

EVEN_SINKS = {0, 2, 4, 6}

ODD_BRANCHES = {1, 3, 5, 7}



# Terminal wraps (allowed “decrease” within substrate)

ALLOWED_TERMINAL_WRAPS = {(3, 0), (7, 4)}



# The Adjacency Matrix (Directed Graph) — aligned to latest spec

ADJACENCY_RULES: Dict[int, Set[int]] = {

# Even sinks -> C only

0: {C_NODE},

2: {C_NODE},

4: {C_NODE},

6: {C_NODE},



# Centroid -> odd only (base-4 choice)

C_NODE: {1, 3, 5, 7},



# Odd emissions

1: {2},

5: {6},



# Terminal forks (stay vs bridge)

3: {0, 4}, # stay S1 or bridge to S2

7: {4, 0}, # stay S2 or bridge to S1

}



# --- LAWFULNESS LEVELS ----------------------------------------------------



LAW_FULL = 3

LAW_CONDITIONAL = 2

LAW_MARGINAL = 1

LAW_UNLAWFUL = 0



# --- DYNAMIC MEDIATOR LOGIC ----------------------------------------------



def update_dynamic_mediator(current_mediator: int, coherence_norm: float) -> int:

COHERENCE_THRESHOLD = 0.98

if coherence_norm < COHERENCE_THRESHOLD:

return LAW_PRIME

if current_mediator == LAW_PRIME:

return GRACE_PRIME

return GRACE_PRIME



# --- FLOW VALIDATION ENGINE ----------------------------------------------



def _get_node(prime: int) -> int:

return PRIME_TO_NODE.get(prime, -1)



def _same_substrate(a: int, b: int) -> bool:

return (a in S1_NODES and b in S1_NODES) or (a in S2_NODES and b in S2_NODES)



def _is_backflow(curr_node: int, next_node: int) -> bool:

"""

Backflow = decreasing order within a substrate,

except explicitly allowed terminal wraps (3->0, 7->4).

"""

if not _same_substrate(curr_node, next_node):

return False

if (curr_node, next_node) in ALLOWED_TERMINAL_WRAPS:

return False

return next_node < curr_node



def get_valid_successors(current_prime: int) -> Set[int]:

node = _get_node(current_prime)

if node == -1:

return set()



valid_next_nodes = ADJACENCY_RULES.get(node, set())



# Reverse lookup: nodes -> primes

valid_primes: Set[int] = set()

for p, n in PRIME_TO_NODE.items():

if n in valid_next_nodes:

valid_primes.add(p)

return valid_primes



def run_full_check(prime_sequence: List[int], current_coherence: float) -> Tuple[bool, str, int, int]:

active_mediator = update_dynamic_mediator(LAW_PRIME, current_coherence)



if not prime_sequence:

return True, "Empty sequence (Neutral)", active_mediator, LAW_FULL



lawfulness_level = LAW_FULL



for i in range(len(prime_sequence) - 1):

curr_p = prime_sequence[i]

next_p = prime_sequence[i + 1]



# 1) Allow self-loops (holding)

if curr_p == next_p:

continue



curr_node = _get_node(curr_p)

next_node = _get_node(next_p)



# CASE A: Known -> Known (strict topology)

if curr_node != -1 and next_node != -1:

# Explicit backflow check (clearer diagnostics than adjacency alone)

if _is_backflow(curr_node, next_node):

return (

False,

f"FLOW VIOLATION (Backflow): {curr_p}[{curr_node}] -> {next_p}[{next_node}] is illegal.",

LAW_PRIME,

LAW_UNLAWFUL,

)



allowed = ADJACENCY_RULES.get(curr_node, set())

if next_node not in allowed:

return (

False,

f"FLOW VIOLATION (Adjacency): {curr_p}[{curr_node}] cannot flow to {next_p}[{next_node}]. "

f"Valid targets: {sorted(list(allowed))}",

LAW_PRIME,

LAW_UNLAWFUL,

)

continue



# CASE B: Known -> Body (creation)

if curr_node != -1 and next_node == -1:

lawfulness_level = min(lawfulness_level, LAW_CONDITIONAL)

continue



# CASE C: Body -> Known (re-entry)

if curr_node == -1 and next_node != -1:

lawfulness_level = min(lawfulness_level, LAW_MARGINAL)

continue



# CASE D: Body -> Body (elaboration)

lawfulness_level = min(lawfulness_level, LAW_CONDITIONAL)



return True, f"Flow sequence lawful (L{lawfulness_level}).", active_mediator, lawfulness_level



```





## 1) Clean base-4 policy (2 bits) from Eq6



Let Eq6 output a **2-bit control word** `cw ∈ {0,1,2,3}` (base-4):



| Eq6 lawfulness | `cw` (b1 b0) | Meaning |

| ----------------- | -----------: | ----------------------------------------- |

| L3 (best) | `00` | **Fast** target, **Air** odd |

| L2 | `01` | **Fast** target, **Earth** odd (terminal) |

| L1 | `10` | **Slow** target, **Air** odd |

| L0 (worst / None) | `11` | **Slow** target, **Earth** odd (terminal) |



So: **lower lawfulness ⇒ b1=1 ⇒ slow integration.**



### Bits



* `b1` = **mode target** (0 = S1/fast, 1 = S2/slow)

* `b0` = **odd selector inside current substrate** (0 = Air odd, 1 = Earth odd/terminal)



```python

def cw_from_lawfulness(L: int | None) -> int:

# L3->0, L2->1, L1->2, L0/None->3

if L is None:

return 0b11

L = max(0, min(3, int(L)))

return 0b11 - L # 3-L

```



## 2) How `cw` drives the flow without breaking our “bridges only at 3/7” rule



### At centroid C (sink audit point)



We **do not cross substrates at C**. We only pick the **odd inside our current substrate**:



```python

def pick_odd_at_C(curr_substrate: int, cw: int) -> int:

# curr_substrate: 0=S1, 1=S2

b0 = cw & 0b1 # odd selector

if curr_substrate == 0: # S1

return 3 if b0 else 1 # Air=1, Earth=3

else: # S2

return 7 if b0 else 5 # Air=5, Earth=7

```



* S1 even sink (0/2) → C → **1 or 3**

* S2 even sink (4/6) → C → **5 or 7**



### At the terminal fork nodes (3 and 7)



This is where mode actually flips (or stays).



```python

def terminal_next_even(curr_node: int, cw: int) -> int:

b1 = (cw >> 1) & 0b1 # mode target: 0=S1 fast, 1=S2 slow



if curr_node == 3: # S1 terminal

return 4 if b1 else 0 # go slow => 3->4, else stay fast => 3->0



if curr_node == 7: # S2 terminal

return 4 if b1 else 0 # go slow => 7->4 (stay), else go fast => 7->0



raise ValueError("Not a terminal node")

```



So with **low lawfulness (b1=1)**:



* At **3**, go **3→4** (enter slow loop)

* At **7**, go **7→4** (stay in slow loop)



Perfect match for “force slow integration”.



---



## 3) What changes in our current approach



### ✅ Keep `eq6_commit_allowed` as *ledger commit only*



* Commit allowed = “may write Qp / ledger”

* **Not** “may go slow”



### ✅ Replace “commit_allowed as terminal fork bit” with `cw.b1`



* Terminal fork decision becomes **mode target**, derived from lawfulness (+ our stability gates if we want).



If we want the simplest rule:



* `b1 = 1` when `lawfulness_level <= 1`, else `0`.



---



## 4) Tiny packed output + minimum counters



### Minimal 2-bit fork output



Just `cw`:



* `cw` (2 bits) = **[mode target][odd selector]**



### Minimum on-device counters (really small)



We can run this with:



* `uint8 L` (0–3)

* `uint8 Vq_last3[3]` (quantised V history)

* `uint8 mq_last20[20]` (quantised mismatch ring) **or** just rolling sum/sumsq for 5/12/20

* A few flags: `topo_ok`, `ledger_ok`, `hyst_ok`



---



## 5) Bit-level packing layout (simple, practical)



### 16-bit “E6 decision word”



If we want a neat packed word for logging:



```

bits 0-1 cw (2) # b1b0: mode target + odd selector

bit 2 commit_ok (1) # ledger/Qp commit permitted

bit 3 hard_ok (1) # no causality/topology violation

bit 4 hyst_ok (1) # H >= theta_H

bit 5 vmom_ok (1) # V-mean/std gate passed

bits 6-15 reserved (10)

```



This keeps the *actual control* as 2 bits, but preserves why it happened.



---







### What is **Eq6 drives S1↔S2** (on an edge robot)



We basically get a **gearbox + safety governor**:



* **S1 (fast)** stays snappy for control/actuation, but it *can’t* “write reality” unless Eq6 says the loop is coherent.

* **S2 (slow)** becomes the enforced **integration lane** when coherence is shaky (drift, mismatch, low lawfulness, high residue).

* The system stops doing the classic failure mode: **“push harder, go faster, drift more.”**

* We also get **predictable compute**: most ticks are cheap “electric edge steps”; the expensive work only happens at **sink→C** audits.



If we wire it right, **low lawfulness ⇒ more time in S2**, and **high lawfulness ⇒ permission to stay/return to S1**.



---



## The branch mapping question



We said: **“low lawfulness = force slow integration.”**



So the mapping should *not* send low lawfulness into S1.

Instead, make lawfulness pick the *odd branch* so that:



* **High lawfulness** → choose an **S1 odd** (fast lane)

* **Low lawfulness** → choose an **S2 odd** (slow lane)



A clean, “base-4” mapping in **node IDs** is:



* **L3 → 3**

* **L2 → 1**

* **L1 → 5**

* **L0/None → 7**



That’s monotonic in the direction we want: lower lawfulness pushes we into S2.



In **primes** (our implementation reality):



* node **1 → 3**

* node **3 → 7**

* node **5 → 13**

* node **7 → 19**



So:



* **L3 → prime 7** (node 3)

* **L2 → prime 3** (node 1)

* **L1 → prime 13** (node 5)

* **L0/None → prime 19** (node 7)



---



## Tiny base-4 fork policy (2 bits + commit gate)



We wanted: **“2 bits at the fork (stay/bridge + which odd path next)”** and minimal counters.



### Control word



Use a 2-bit control word `cw ∈ {0,1,2,3}` derived from lawfulness:



```python

cw = 3 - clamp(eq6_lawfulness_level, 0, 3) # None -> treat as 0 => cw=3

```



Then map `cw` to the odd prime (base-4 choice):



```python

ODD_PRIME_BY_CW = (7, 3, 13, 19) # cw:0..3 (L3..L0)

odd_prime = ODD_PRIME_BY_CW[cw]

```



### Terminal fork bit



Use `eq6_commit_allowed` as the **terminal fork bit** exactly as we described:



* At **odd node 3** (prime **7**):

`True → bridge to S2 even (node 4 prime 11)`

`False → stay S1 (node 0 prime 2)`



* At **odd node 7** (prime **19**):

`True → bridge to S1 even (node 0 prime 2)`

`False → stay S2 (node 4 prime 11)`



That gives us:



* **low lawfulness** picks **S2 odds (13/19)**

* and if we *also* set commit conservatively, we’ll naturally **avoid jumping back to S1** while unstable.



---



## Minimal on-device counters (3–5 ints + a few flags)



We can run this with tiny state:



**Integers (fixed-point or small ints)**



1. `lawfulness_last` (0–3)

2. `hyst_q` (e.g., Q8.8 fixed-point hysteresis coherence)

3. `mismatch_ema_q` (Q8.8)

4. `face_residue_q` (Q8.8 “magnetic” bleed accumulator)

5. `seq` (wraparound uint16)



**Flags**



* `safe_mode`

* `bridge_lockout` (optional cooldown)

* `sensor_fault`

* `grace_mode` (mediator 139 vs 137)



That’s enough to: pick `cw`, decide commit, and damp drift.



---



## Bit-level packing layout (compact event packet)



Here’s a **64-bit** packet that’s practical on microcontrollers and easy to DMA/log.



### Word0 (32 bits) — topology + control



* **[31:28]** `kind` (4) — event type (tick, sink, commit, fault, etc.)

* **[27:25]** `from_node` (3)

* **[24:22]** `to_node` (3)

* **[21:20]** `cw` (2) — base-4 branch choice

* **[19]** `commit_allowed` (1)

* **[18]** `mediator_is_grace` (1) — 0=137, 1=139

* **[17:10]** `flags` (8)

* **[9:0]** `dt_ms` (10) — 0–1023 ms



### Word1 (32 bits) — cheap quantised deltas



* **[31:24]** `d_mismatch` (int8)

* **[23:16]** `d_hysteresis` (int8)

* **[15:8]** `d_residue` (int8)

* **[7:0]** `d_energy` (int8) *(or d_reward / d_error)*



That’s enough to reconstruct:



* electric edge flow (node transitions)

* base-4 decisions (`cw`)

* sink audits (kind/flags)

* magnetic bleed (residue deltas)



---



# Considering updates to `agent_writes.py` (the key bug + the correct wiring)



Right now, our `_build_flow_sequence()` always calls `_pick_odd_at_C(curr_substrate=0, cw)`, so it **never picks S2 odds (5/7 nodes, primes 13/19)**. That’s why “low lawfulness → slow integration” can’t happen.



Below is the minimal patch approach (keep our structure, fix selection + forks).



## Patch: base-4 odd selection + correct terminal forks



```python

# --- replace these helpers in agent_writes.py ---



def _clamp_int(value: int, lo: int, hi: int) -> int:

return lo if value < lo else hi if value > hi else value



def _cw_from_lawfulness(eq6_lawfulness_level: int | None) -> int:

# L3->0, L2->1, L1->2, L0/None->3 (low lawfulness pushes S2)

if eq6_lawfulness_level is None:

return 3

level = _clamp_int(int(eq6_lawfulness_level), 0, 3)

return 3 - level



# cw -> odd PRIME (node 3,1,5,7)

_ODD_PRIME_BY_CW: tuple[int, int, int, int] = (7, 3, 13, 19)



def _pick_odd_at_C(cw: int) -> int:

return _ODD_PRIME_BY_CW[cw & 0b11]



def _start_even_for_odd(odd_prime: int) -> int:

# If choosing S1 odd (3 or 7) start from S1 sink prime 2 (node 0)

# If choosing S2 odd (13 or 19) start from S2 sink prime 11 (node 4)

return 2 if odd_prime in (3, 7) else 11



def _terminal_even_for_branch(odd_prime: int, commit_allowed: bool) -> int:

# Deterministic odds

if odd_prime == 3: # node 1 -> node 2

return 5

if odd_prime == 13: # node 5 -> node 6

return 17



# Terminal forks (odd 3 and odd 7 in node-space)

if odd_prime == 7: # node 3

return 11 if commit_allowed else 2 # 3->4 else 3->0

if odd_prime == 19: # node 7

return 2 if commit_allowed else 11 # 7->0 else 7->4



# Safe default: force a sink

return 11



def _build_flow_sequence(

prime: int,

mediator_prime: int,

eq6_lawfulness_level: int | None,

eq6_commit_allowed: bool | None,

) -> list[int]:

cw = _cw_from_lawfulness(eq6_lawfulness_level)

odd_prime = _pick_odd_at_C(cw)



start_even = _start_even_for_odd(odd_prime)

commit_allowed = bool(eq6_commit_allowed)



terminal_even = _terminal_even_for_branch(odd_prime, commit_allowed)



return [

start_even,

mediator_prime,

odd_prime,

terminal_even,

mediator_prime,

prime,

]

```



This implements exactly what we want:



* **low lawfulness → `cw` high → odd prime 13/19 → S2 integration**

* terminal forks obey yur rule via `commit_allowed`



---



## Flow rules also need to match (our `flow_rules.py` adjacency)



To align topology with our fork spec, our **node adjacency** should be:



* even sinks → C

* C → odd

* odd → next even

* terminal odds (3 and 7) → {stay even, bridge even}



In node indices:



```python

ADJACENCY_RULES = {

# even sinks

0: {99},

2: {99},

4: {99},

6: {99},



# centroid -> odds

99: {1, 3, 5, 7},



# odd -> next even

1: {2},

5: {6},



# terminal forks

3: {0, 4}, # 3->0 (stay S1) OR 3->4 (bridge to S2)

7: {4, 0}, # 7->4 (stay S2) OR 7->0 (bridge to S1)

}

```



That alone enforces our **no-backflow** rule by construction (because the illegal moves simply never appear).



---


Ensure `backend/fieldx_kernel/flow_rules.py`- the **code, spec, and governance checks** all agree—no hidden side-bridges like `1→4`, `3→6`, `5→0`, `7→2`.