# InnerShiftV

> **“The signal is the structural tension; the noise is the price.”**

---

## 1. The Core Anchor

This project exists to automate the **Forensic Skepticism** that led to the Archer Daniels Midland discovery in the 90s.

In a market dominated by **whales** and passive index flows, **InnerShiftV** is the sovereign auditor.

We are not trying to “beat” the market.  
We are identifying where the market’s plumbing has failed to reflect reality.

---

## Grounding Principles

* **Privacy is Alpha:** Financial strategy is intellectual property. This system runs on local silicon — specifically the **Mac Studio M1** — to ensure zero data leakage to third-party cloud providers.

* **Deterministic Focus:**  We prefer rule-based, deterministic logic over black-box neural networks.We seek the **force behind the number**, not just the number itself.

* **Cumulative Micro-Bets:** Success is measured by the consistent scavenging of small efficiency leaks, not the pursuit of high-risk "home runs".

---

## 2. Structural Sentinel Goals

* **Correlation Decay:** Monitor the “tethers” between historically linked assets, such as specific commodities and their equity proxies.

Flag the moment they decouple without a valid news catalyst.

* **Variance Auditing:** Identify "Impossible Profiles" -Securities with high returns and unnaturally low variance that suggest non-market intervention, price suppression, or price-fixing

* **MOC Scavenging:** Monitor the forced liquidity crunch at the **Market-on-Close** window. The goal is to capture predictable price drifts caused by passive index flows.

---

## 3. Sovereign Architecture

* **Intelligence:** Local inference via **Ollama** using **Llama 3**.  No metered cloud costs.  No strategy leakage

* **Governance:** Orchestrated by **CrewAI**. Future rigid execution rules may be handled by **LangGraph**.

* **Data Pipes:**  **Alpha Vantage MCP** for institutional feeds  **CCXT** for cross-exchange arbitrage monitoring

* **Isolation:** The system runs entirely within **Docker**. The "Sentinel" should remain always-on and decoupled from daily development work.

---

## 4. Safety Protocols (For Future Me)

* **The "NA" Fail-Safe:** The ```OPENAI_API_KEY``` is hard-coded to: ```"NA"```. If the system tries to call the cloud, it must fail. We do not leak strategy.

* **Telemetry Opt-Out:** ```CREWAI_TELEMETRY_OPT_OUT=true``` is mandatory in the environment.
* **Human-in-the-Loop:**  Before any live execution, even $1.00, the **Skeptic Analyst** must present a forensic report for human approval in the terminal. No autonomous trading without human review.

---

## 5. Startup Checklist

1. Verify Ollama is Running ```ollama run llama3```
 
2. Initialize the Environment:** ```uv venv``` and ```source .venv/bin/activate```.

3. Run the Shadow Sentinel: ```crewai run``` (Current Mission: Relationship- Monitor)

---

## 6. Execution Phases

1. Phase 1 (Shadow/Paper): Audit the "V" signals for 30 days without spending a cent.´®
2. Phase 2 (Cumulative Micro-Bets): Execute $1.00 trades to test the "plumbing" and fees.
3. Phase 3 (Scaling): Only increase stakes once the fees vs. gain math is proven.
