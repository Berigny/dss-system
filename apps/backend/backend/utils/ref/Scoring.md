

## Mandatory Direction: Always-Write, Selective Promotion (Non-negotiable)

**E6 MUST NOT block recording. E6 ONLY blocks promotion.**

1. **Always-Write (Raw Diary Ledger) — MUST**

* Every operational tick / micro-episode **SHALL** append a minimal, hash-chained **Raw Event** to the local ledger (ℚ layer), **in every mode** (including **Mode 0 HALT**).
* Raw events are **append-only**, low-cost, and tamper-evident.
* Raw events **do not imply correctness**. They are “what happened”, not “what we believe”.

**Raw Event minimum fields (edge-friendly):**

* `seq`, `t_ms`, `mode` (0–3), `ptype` (WU/HR/PP/CA), `law` (0–3), `route` (0–3)
* provenance stubs: `inputs_stub_hash64`, `schema_hash64`, `version_hash32`
* ledger link: `prev_hash64`, `hash64`, `prov_commit64`
* minimal metrics: `dE2_q`, `dB2_q`, `dW`, `L_q`, `H_q`, `A_q`, `U_q`, `V_q`, `K`, `P`, `E`
* optional tiny tags: `action_tag`, `sensor_tag`

2. **Selective Promotion (Canonical / Retrievable Memory) — E6-Gated**

* A Raw Event **MAY** be promoted to a **Promotion Ledger / Canonical Memory tier** *only if* E6 gates pass.
* Promotion is the only act that makes information:

  * **retrieval-worthy**,
  * **policy/skill-affecting**,
  * **shareable as “stable truth”** (WU).

3. **Promotion rule — MUST**

* Promotion **SHALL** require:

  * hard gates: `K=1`, `P=1`, `E=1`, `L_top=1` (admissible `ΔW`)
  * stability: `H ≥ θH`
  * momentum: `mean(V_last3) ≥ θV` and `var(V_last3) ≤ θσ`
* Implemented as:
  `BridgeAllowed(t) = 1[P=1] * 1[E=1] * 1[L ≥ θL] * 1[H ≥ θH] * 1[mean(V) ≥ θV] * 1[var(V) ≤ θσ]`

4. **Route semantics (header `route` field) — MUST**

* `route=0` **block**: record raw only; no promotion; no “learning merge”
* `route=1` **quarantine**: record raw; flag for review; no promotion
* `route=2` **local-commit**: record raw + allow device-local consolidation (e.g., calibration); limited promotion tier
* `route=3` **ledger-commit**: record raw + promote to canonical (WU allowed)

5. **Mode behaviour alignment — MUST**

* **Mode 0 (HALT):** record Raw Events + HR; **no promotion**, no high-impact actions.
* **Mode 1 (PROBE):** record Raw Events + PP; promotion normally **blocked/quarantined**.
* **Mode 2 (STABILISE):** record Raw Events + CA; **local-commit** promotion allowed when stable.
* **Mode 3 (EXPRESS):** record Raw Events + WU; **ledger-commit** promotion allowed when momentum is stable.

6. **Review is triggered, not universal — SHOULD**

* Not all Raw Events are reviewed more than once. Re-review **SHOULD** be triggered by:

  * contradiction / self-model violation,
  * HR/strain spike,
  * novelty spike / new context class,
  * prediction error spike,
  * repeated-pattern frequency,
  * human feedback/override.

7. **Operational invariant — MUST (testable)**

* Under any hard violation (e.g., `K=0` or `P=0` or inadmissible `ΔW`), the system:

  * **SHALL still append a Raw Event**, and
  * **SHALL NOT promote** that event.

**Bottom line:** the system mirrors intelligent behaviour by **remembering everything once** (raw diary), while only **believing/using** what survives E6’s non-compensatory gates (promotion).

---



% ==========================================================
% APPENDIX (ONE-PAGER): E6 SOVEREIGNTY ENGINE SUMMARY
% ==========================================================
\section{Appendix (One-page): E6 Sovereignty Engine Summary}
\label{app:e6_onepager}

\vspace{-0.25\baselineskip}
\begin{equation}
\label{eq:bridgeallowed_onepager}
\mathrm{BridgeAllowed}(t) :=
\Ind[P_t=1]\cdot \Ind[E_t=1]\cdot \Ind[L_{\mathrm{top}}(t)=1]\cdot \Ind[K_t=1]\cdot
\Ind[L_{\mathrm{phys}}(t)\ge\theta_L]\cdot \Ind[H_t\ge\theta_H]\cdot
\Ind[\overline{V}^{\mathrm{int}}_{t,3}\ge\theta_V]\cdot \Ind[\sigma^{\mathrm{int}}_{t,3}\le\theta_\sigma].
\end{equation}

\vspace{-0.5\baselineskip}
\begin{equation}
\label{eq:telos_onepager}
T_t := \mathrm{BridgeAllowed}(t)\cdot \overline{V}^{\mathrm{int}}_{t,3}\cdot \mathrm{Impact}_t,
\qquad
\mathrm{Impact}_t := (A^{\mathrm{ext}}_t\cdot L^{\mathrm{ext}}_t\cdot Lo^{\mathrm{ext}}_t)\cdot \log(1+N_{\mathrm{verified}})\cdot c_t.
\end{equation}

\vspace{-0.25\baselineskip}
\small
\begin{table}[htbp]
\centering
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.15}
\begin{tabularx}{\textwidth}{@{} l l c X X @{}}
\toprule
\textbf{Block} & \textbf{Signal} & \textbf{Type} & \textbf{Pass rule (hard-first)} & \textbf{Fail response (mode/route/packet)} \\
\midrule
\multicolumn{5}{@{}l@{}}{\textbf{Hard gates (must pass; non-compensatory)}}\\
Topology (Eq3) & $L_{\mathrm{top}}$ & Hard & $\Delta W \in \mathcal{A}_W$ (typical $\{0,\pm1\}$) & \textbf{HALT}, route=block, \textbf{HR} \\
Ledger (Eq2) & $K_t$ & Hard & hash-chain valid \& deterministic replay matches & \textbf{HALT}, route=block, \textbf{HR} \\
Provenance & $P_t$ & Hard & schema + inputs + version pinned + replayable & \textbf{HALT}, route=block, \textbf{HR} \\
Ethics (Eq8) & $E_t$ & Hard & admissible action-set; \textbf{non-bypass} enforced & \makecell[l]{If high-impact action: \textbf{HALT} (block)\\Else: \textbf{STABILISE} (quarantine), \textbf{CA/HR}} \\
\midrule
\multicolumn{5}{@{}l@{}}{\textbf{Soft gates (only evaluated if hard gates passed)}}\\
Lawfulness (soft) & $L_{\mathrm{phys}}$ & Soft & $\exp(-\lambda_1|\Delta I_1|-\lambda_2|\Delta I_2|)\ge\theta_L$ & \textbf{PROBE}, route=quarantine, \textbf{PP} \\
Hysteresis & $H_t$ & Soft & stability over window + trend not worsening: $H_t\ge\theta_H$ & \textbf{PROBE}, route=quarantine, \textbf{PP} \\
Witnessing floor (Eq6) & $A^{\mathrm{self}}_t$ & Soft (floor) & $\exp(-\kappa\,\mathrm{viol}(g_t))\ge\theta_{\mathrm{self}}$ & \textbf{HALT} or \textbf{PROBE} (policy), \textbf{HR/PP} \\
Awareness (Eq6) & $A_t$ & Soft & $A_t=A^{\mathrm{corr}}_t\cdot A^{\mathrm{self}}_t$ (non-comp.) & remain \textbf{PROBE} until stable \\
Unity (Eq7) & $U_t$ & Soft & continuity score over admissible region & degrade to \textbf{STABILISE} or \textbf{PROBE} \\
Internal viability (Eq9 core) & $V^{\mathrm{int}}_t$ & Soft & $V^{\mathrm{int}}_t=A_t\cdot U_t$ & lower mode; no WU \\
Momentum gate & $\overline{V}^{\mathrm{int}}_{t,3},\sigma^{\mathrm{int}}_{t,3}$ & Soft (gate) & mean $\ge\theta_V$ and std $\le\theta_\sigma$ & cannot enter \textbf{EXPRESS} \\
\midrule
\multicolumn{5}{@{}l@{}}{\textbf{Base-4 modes (what is allowed)}}\\
Mode 0 & HALT & --- & freeze/safe posture; no learning commits & packets: \textbf{HR} only; route=block \\
Mode 1 & PROBE & --- & tiny reversible tests; evidence gather & packets: \textbf{PP} + HR; route=quarantine \\
Mode 2 & STABILISE & --- & normal ops + calibrations; safe commits & packets: \textbf{CA} + HR; route=local-commit \\
Mode 3 & EXPRESS & --- & high-impact actions; publish stable truth & packets: \textbf{WU} (+HR); route=ledger-commit \\
\midrule
\multicolumn{5}{@{}l@{}}{\textbf{Telos coupling (internal $\rightarrow$ external)}}\\
External impact & $\mathrm{Impact}_t$ & Soft (weighted) & \makecell[l]{Only count if \textbf{confirmed} (not inferred):\\Awareness uplift $\times$ Life uplift $\times$ Love/dignity uplift\\$\times$ verified reach $\times$ confidence} & If unconfirmed: $\mathrm{Impact}_t\to 0$ (no extra weight) \\
Telos reinforcement & $T_t$ & Soft (weight) & Only if BridgeAllowed=1; increases retrievability/weight & Never bypasses hard gates; only affects memory weighting \\
\bottomrule
\end{tabularx}
\caption{E6 one-page spec: hard-first gates, base-4 modes, packet permissions, and telos coupling.}
\end{table}
\normalsize



% ==========================================================
% APPENDIX: E6 SOVEREIGNTY ENGINE (EDGE ROBOT / DUAL SUBSTRATE)
% ==========================================================
\appendix
\section{Appendix: E6 Sovereignty Engine Spec (Edge Robot, Dual Substrate)}
\label{app:e6_spec}

\subsection{A1. Purpose (what E6 is)}
E6 is the robot’s \textbf{non-compensatory referee + diary clerk} that decides, on each tick (or micro-episode), whether a candidate update is:
(i) \textbf{lawful}, (ii) \textbf{stable}, (iii) \textbf{provenanced}, (iv) \textbf{admissible}, and therefore (v) \textbf{worth committing} into the $\Qp$ ledger so learning does not drift.

\textbf{Design rule (non-compensatory):} soft scores can \emph{never} buy a pass through a hard failure.

\subsection{A2. Operating loop (base-4)}
E6 operates as a \textbf{base-4 state machine}:
\[
\text{Mode}\in\{0,1,2,3\}=\{\text{HALT},\text{PROBE},\text{STABILISE},\text{EXPRESS}\}.
\]
Mode gates (a) what actions are allowed and (b) which packet types may be emitted or committed.

\subsection{A3. Inputs and outputs (tick contract)}
\textbf{Inputs per tick:}
\begin{itemize}
  \item Fast-layer deltas (quantised): $\Delta E, \Delta B, \Delta W$ (or their structural analogues).
  \item Ledger link fields: previous hash, current hash, provenance commitment.
  \item Declared intent/constraints for the tick (for witnessing).
  \item Rolling window state: last $w$ mismatch values; last 3 viability values.
\end{itemize}

\textbf{Outputs per tick:}
\begin{itemize}
  \item Next \textbf{Mode} (0--3) and \textbf{PacketType} (WU/HR/PP/CA).
  \item \textbf{Route} decision: block / quarantine / local-commit / ledger-commit.
  \item Optional \textbf{Telos reinforcement} scalar for memory weighting (only if allowed).
\end{itemize}

\subsection{A4. Gate metrics (hard-first, then soft)}
All scalars are normalised to $[0,1]$ and stored as fixed-point (e.g.\ Q0.16). Hard gates are binary.

\subsubsection*{A4.1 Hard gates (must pass)}
\paragraph{Topology gate (Eq3):}
\[
L_{\mathrm{top}}(t) := \Ind[\Delta W \in \mathcal{A}_W] \quad \text{(default: } \mathcal{A}_W=\{0,\pm 1\}\text{)}.
\]

\paragraph{Ledger integrity (Eq2):}
\[
K_t\in\{0,1\},\quad K_t=1 \Leftrightarrow \text{hash-chain valid \& deterministic replay matches state}.
\]

\paragraph{Provenance completeness:}
\[
P_t := \Ind[\text{schema complete}]\cdot\Ind[\text{inputs logged}]\cdot\Ind[\text{version pinned}]\cdot\Ind[\text{replayable}].
\]

\paragraph{Ethics admissibility (Eq8):}
\[
E_t := \Ind[X_t \in \mathcal{E}],
\]
where $\mathcal{E}$ includes \textbf{non-bypass}: no action/tool class is allowed without provenance + policy gating.

\subsubsection*{A4.2 Soft gates (graded, only after hard pass)}
\paragraph{Lawfulness (soft part only):}
\[
L_{\mathrm{phys}}(t)=\exp\big(-\lambda_1|\Delta I_1|-\lambda_2|\Delta I_2|\big),
\quad
L_t := L_{\mathrm{phys}}(t)\cdot L_{\mathrm{top}}(t)\cdot K_t.
\]

\paragraph{Hysteresis / stability over history:}
Let mismatch $\Delta(t)=|\Delta I_1|+|\Delta I_2|+\eta|\Delta W|$.
\[
H_t := \exp\Big(-\mu\,\mathrm{Var}_{u\in[t-w,t]}\Delta(u)\Big)\cdot
\exp\big(-\mu_2\max(0,\mathrm{slope}(\Delta))\big).
\]

\paragraph{Awareness (Eq6, non-compensatory):}
\[
A_t := A^{\mathrm{corr}}_t\cdot A^{\mathrm{self}}_t,\qquad
A^{\mathrm{self}}_t=\exp\big(-\kappa\,\mathrm{viol}(g_t)\big),
\]
with \textbf{required floor} $A^{\mathrm{self}}_t \ge \theta_{\mathrm{self}}$.

\paragraph{Unity (Eq7):}
\[
U_t := \exp\!\big(-\alpha_1|\Delta I_1|-\alpha_2|\Delta I_2|-\alpha_3|\Delta W|\big),
\]
with inadmissible $\Delta W$ handled as a hard fail via $L_{\mathrm{top}}$ (so $U_t$ stays meaningful).

\paragraph{Internal viability (Eq9 core):}
\[
V^{\mathrm{int}}_t := A_t\cdot U_t.
\]

\paragraph{Momentum constraint (anti-spike):}
Over last 3 ticks:
\[
\overline{V}^{\mathrm{int}}_{t,3} := \mathrm{mean}(V^{\mathrm{int}}_{t-2:t}),\quad
\sigma^{\mathrm{int}}_{t,3} := \mathrm{std}(V^{\mathrm{int}}_{t-2:t}).
\]

\subsection{A5. BridgeAllowed (single source of truth)}
Bridge release is a strict product of hard-first conditions:
\[
\mathrm{BridgeAllowed}(t) :=
\Ind[P_t=1]\cdot \Ind[E_t=1]\cdot
\Ind[L_t \ge \theta_L]\cdot
\Ind[H_t \ge \theta_H]\cdot
\Ind[\overline{V}^{\mathrm{int}}_{t,3} \ge \theta_V]\cdot
\Ind[\sigma^{\mathrm{int}}_{t,3}\le \theta_\sigma].
\]
If $\mathrm{BridgeAllowed}(t)=0$, the tick may still emit telemetry, but must not produce a high-trust ledger merge.

\subsection{A6. Internal vs External optimisation towards telos}
E6 optimises \textbf{internal coherence first}, then uses \textbf{external impact} to decide \emph{what becomes heavier / more retrievable} over time.

\subsubsection*{A6.1 External impact (human-world objective)}
Define external impact as:
\[
\mathrm{Impact}_t :=
(A^{\mathrm{ext}}_t\cdot L^{\mathrm{ext}}_t\cdot Lo^{\mathrm{ext}}_t)\cdot N_{\mathrm{eff}}(t)\cdot c_t,
\]
where each term is in $[0,1]$:
\begin{itemize}
  \item $A^{\mathrm{ext}}_t$ (Awareness uplift): user/operator clarity confirmed (not inferred).
  \item $L^{\mathrm{ext}}_t$ (Life uplift): measurable outcome improvement confirmed (task completion, time/error reduction).
  \item $Lo^{\mathrm{ext}}_t$ (Love/dignity uplift): improved trust/care/dignity confirmed; \textbf{no coercion or dependency allowed}.
  \item $N_{\mathrm{eff}}(t) := \log(1+N_{\mathrm{verified}})$ (dampened ripple; verified only).
  \item $c_t$ confidence (starts low, rises with confirmations over time).
\end{itemize}

\textbf{Anti-gaming rule:} $A^{\mathrm{ext}},L^{\mathrm{ext}},Lo^{\mathrm{ext}}$ are only counted when confirmed by explicit signal (user report / operator check / downstream adoption evidence). Never score by mere model inference.

\subsubsection*{A6.2 Telos reinforcement (what gets to stick)}
Telos reinforcement is:
\[
T_t := \mathrm{BridgeAllowed}(t)\cdot \overline{V}^{\mathrm{int}}_{t,3}\cdot \mathrm{Impact}_t.
\]
Interpretation:
\begin{itemize}
  \item \textbf{Internal pass} ($\mathrm{BridgeAllowed}=1$) is mandatory for any telos reinforcement.
  \item \textbf{External impact} decides the \emph{weighting} of the commit (retrievability / priority), not whether a hard gate is allowed to be bypassed.
\end{itemize}

\subsection{A7. Mode transitions (minimal, practical)}
\subsubsection*{A7.1 Hard fail to HALT}
\[
\text{ANY} \rightarrow \text{HALT}
\]
if any hard violation occurs:
\begin{itemize}
  \item causality cap exceeded (e.g.\ $\|\Delta E\|^2 > \mathrm{MAX\_DE2}$ or $\|\Delta B\|^2 > \mathrm{MAX\_DB2}$),
  \item topology jump ($\Delta W\notin\mathcal{A}_W$),
  \item ledger broken ($K_t=0$),
  \item provenance broken ($P_t=0$),
  \item ethics fail ($E_t=0$) when action class is high-impact.
\end{itemize}

\subsubsection*{A7.2 Recovery and climb}
\begin{itemize}
  \item HALT $\rightarrow$ PROBE when recovery condition is met (human clear, or stable self-test).
  \item PROBE $\rightarrow$ STABILISE when $K_t=1,P_t=1,L_t\ge\theta_L,H_t\ge\theta_H$.
  \item STABILISE $\rightarrow$ EXPRESS when $\overline{V}^{\mathrm{int}}_{t,3}\ge\theta_V$ and $\sigma^{\mathrm{int}}_{t,3}\le\theta_\sigma$.
  \item EXPRESS $\rightarrow$ STABILISE if drift rises, novelty spikes uncertainty, or $\sigma^{\mathrm{int}}$ increases.
  \item STABILISE $\rightarrow$ PROBE if uncertainty rises and requires sandbox testing.
\end{itemize}

\subsection{A8. Packet gating (WU / HR / PP / CA)}
\begin{itemize}
  \item \textbf{HR (Heat Report):} permitted in all modes (especially HALT/PROBE).
  \item \textbf{PP (Pulse Packet):} permitted in PROBE (reversible, low confidence).
  \item \textbf{CA (Calibration Adjustment):} permitted in STABILISE (local tuning, boundary updates).
  \item \textbf{WU (World Update):} permitted in EXPRESS only; may be ledger-committed when $\mathrm{BridgeAllowed}=1$.
\end{itemize}

\subsection{A9. Edge packet format (v0) and 128-bit header}
All base-4 decision fields are \textbf{literal 2-bit digits}: \texttt{mode}, \texttt{ptype}, \texttt{law}, \texttt{route}.

\subsubsection*{A9.1 128-bit header v0 (16 bytes)}
Bits numbered [127:0] (MSB $\rightarrow$ LSB):

\begin{table}[htbp]
\centering
\small
\begin{tabularx}{\textwidth}{@{} l r l X @{}}
\toprule
\textbf{Bits} & \textbf{Size} & \textbf{Name} & \textbf{Notes} \\
\midrule
127--112 & 16 & magic & \texttt{0x4347} (``CG'') \\
111--108 & 4  & ver   & protocol version \\
107--104 & 4  & fmt   & header format (0 = this layout) \\
103--102 & 2  & mode  & 0 HALT, 1 PROBE, 2 STABILISE, 3 EXPRESS \\
101--100 & 2  & ptype & 0 WU, 1 HR, 2 PP, 3 CA \\
99--98   & 2  & law   & lawfulness level 0--3 \\
97--96   & 2  & route & 0 block, 1 quarantine, 2 local-commit, 3 ledger-commit \\
95--92   & 4  & node  & 0--7 (8 nodes; 8--15 reserved) \\
91--88   & 4  & flags & bit0=K, bit1=P, bit2=E, bit3=valid \\
87--80   & 8  & dW    & signed i8 (two's complement) \\
79--56   & 24 & seq   & monotonic sequence (wrap ok) \\
55--32   & 24 & t\_ms  & ms modulo $\sim$4.66 hours \\
31--16   & 16 & V\_q   & Q0.16 internal viability (0..65535) \\
15--0    & 16 & crc16 & CRC-16/CCITT-FALSE over bits [127..16] \\
\bottomrule
\end{tabularx}
\end{table}

\subsubsection*{A9.2 Byte packing (exact)}
Byte 3 packs the four 2-bit digits:
\[
b3=(\mathrm{mode}\ll 6)\;|\;(\mathrm{ptype}\ll 4)\;|\;(\mathrm{law}\ll 2)\;|\;\mathrm{route}.
\]
Byte 4 packs node and flags:
\[
b4=(\mathrm{node}\ll 4)\;|\;\mathrm{flags}.
\]
CRC is CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF).

\paragraph{Recommended hardening (optional):} append an 8-byte keyed MAC (SipHash/BLAKE3-keyed) for tamper resistance on-device.

\subsection{A10. On-device compute budget (O(1) per tick)}
\textbf{State kept:}
\begin{itemize}
  \item $V^{\mathrm{int}}$ ring buffer of length 3.
  \item mismatch statistics for windows (e.g.\ EWMA or running variance for $w\in\{5,12,20\}$).
  \item last ledger hash (64-bit truncation allowed locally; verify fully off-device if needed).
\end{itemize}

\textbf{Compute per tick (cheap path):}
\begin{enumerate}
  \item Hard fail checks (squared norms, no square roots).
  \item Provenance commit + ledger hash check $\Rightarrow (P_t,K_t)$.
  \item Hysteresis from running variance + trend penalty $\Rightarrow H_t$.
  \item Awareness witnessing count $\Rightarrow A^{\mathrm{self}}$ floor check.
  \item Compute $V^{\mathrm{int}}_t=A_tU_t$, update last-3 momentum.
  \item Determine Mode + PacketType + Route.
  \item If $\mathrm{BridgeAllowed}=1$, compute $\mathrm{Impact}_t$ (if confirmed), then $T_t$, then commit (as allowed by mode).
\end{enumerate}

\subsection{A11. Parameter defaults (starting points)}
\begin{table}[htbp]
\centering
\small
\begin{tabularx}{\textwidth}{@{} l l X @{}}
\toprule
\textbf{Parameter} & \textbf{Typical start} & \textbf{Meaning} \\
\midrule
$\theta_L$ & 0.85 & minimum lawfulness (soft term) once hard gates pass \\
$\theta_H$ & 0.80 & minimum stability / hysteresis score \\
$\theta_V$ & 0.85 & minimum mean internal viability over last 3 ticks \\
$\theta_\sigma$ & 0.10 & max std dev of viability over last 3 ticks \\
$\theta_{\mathrm{self}}$ & 0.90 & minimum witnessing (self-closure) floor \\
$\mathcal{A}_W$ & $\{0,\pm1\}$ & admissible topology delta set \\
$N_{\mathrm{eff}}$ & $\log(1+N)$ & dampened ripple factor (verified only) \\
\bottomrule
\end{tabularx}
\end{table}

\textbf{Note:} tune thresholds to environment. Safety-critical robots typically push $\theta_{\mathrm{self}}$ and $\theta_H$ higher, and tighten $\mathcal{A}_W$ (often $\{0\}$).


