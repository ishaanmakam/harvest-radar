"""Exact machine dispatch as a mixed-integer programme (scipy.optimize.milp, HiGHS).

Phase 1 - how many machines go to each zone each day (the hard part):
  Variables   m[z,t]    integer >= 0   machines working in zone z on day t
              x[c,z,t]  real    >= 0   of those, machines coming from CHC c   (only when dist(c,z) <= max_service_km)
              y[j,t]    real    >= 0   ha of job j cleared on day t            (only for open_j <= t <= deadline_j)
  Constraints sum_z x[c,z,t] <= machines_c                 the CHC's fleet, per day
              sum_c x[c,z,t]  = m[z,t]                      machines in a zone come from reachable CHCs
              sum_{j in z} y[j,t] <= rate * m[z,t]          one machine-day clears `rate` ha in one zone
              sum_t y[j,t] <= ha_j                          a job is cleared at most once
  Objective   minimise ha missing the sowing window  (= total_ha - sum y), with a tiny cost per machine-day
              so the solver does not scatter idle machines.
  Only m is integer: for a fixed integer m the x part is a transportation problem, whose vertices are
  integral, so nothing is lost and the search tree is far smaller than with per-CHC integers.

Phase 2 - which CHC sends them (travel tie-break): with the machine-days fixed from phase 1 (trimmed to
  what the cleared area actually needs), an integer transportation problem picks the CHC per machine
  minimising total km. It is tiny and solves in milliseconds.

Jobs sharing (zone, open day, deadline) are merged first: they are interchangeable, and it keeps the model small.
Returns None when scipy is missing or phase 1 finds nothing within the time limit; routing.py then keeps the
greedy plan (and always reports both).
"""
import math
import time
from collections import defaultdict
from datetime import timedelta

from common import d, haversine_km, s

EPS_MACHINE = 1e-5     # ha-equivalent cost per machine-day: breaks ties only
EPS_KM = 1e-6          # ha-equivalent cost per km of CHC->zone travel: steers the schedule towards near CHCs.
                       # On mock: same area as with 0, a third less driving, 0.75 s; 1e-5 is 2x slower, 1e-4 times out
MIP_REL_GAP = 1e-6     # tight, so the served area is exact to ~1e-3 ha and comparable with the greedy plan


def aggregate_jobs(jobs, as_of):
    agg = defaultdict(float)
    for j in jobs:
        agg[(j["zone_id"], max(j["ready"], as_of), j["deadline"])] += j["ha"]
    return [{"zone_id": z, "open": o, "deadline": dl, "ha": ha} for (z, o, dl), ha in sorted(agg.items())]


def _empty(total):
    return {"ha_served_in_window": 0.0, "ha_missed_window": round(total, 1), "pct_served": 0.0}


class _Rows:
    """Sparse constraint builder: row(key, lo, hi) returns the row index, creating it on first use."""

    def __init__(self):
        self.idx, self.lo, self.hi, self.r, self.c, self.v = {}, [], [], [], [], []

    def row(self, key, lo, hi):
        if key not in self.idx:
            self.idx[key] = len(self.lo)
            self.lo.append(lo)
            self.hi.append(hi)
        return self.idx[key]

    def add(self, key, lo, hi, col, val):
        self.r.append(self.row(key, lo, hi))
        self.c.append(col)
        self.v.append(val)


def _assign_chcs(xv, need, chcs, dist, time_limit_s):
    """Phase 2: integer machines x[c,z,t] from CHCs to zone-days, meeting `need` exactly, minimising km."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    sel = [i for i, (ci, z, k) in enumerate(xv) if need.get((z, k), 0) > 0]
    if not sel:
        return {}, "nothing to assign"
    R = _Rows()
    for col, i in enumerate(sel):
        ci, z, k = xv[i]
        R.add(("fleet", ci, k), -np.inf, chcs[ci]["machines"], col, 1.0)
        R.add(("need", z, k), need[(z, k)], need[(z, k)], col, 1.0)
    A = coo_matrix((R.v, (R.r, R.c)), shape=(len(R.lo), len(sel))).tocsr()
    cost = np.array([dist[(chcs[xv[i][0]]["chc_id"], xv[i][1])] for i in sel])
    ub = np.array([chcs[xv[i][0]]["machines"] for i in sel], dtype=float)
    res = milp(cost, constraints=LinearConstraint(A, np.array(R.lo), np.array(R.hi)), integrality=np.ones(len(sel)),
               bounds=Bounds(np.zeros(len(sel)), ub), options={"time_limit": float(time_limit_s), "disp": False})
    if res.x is None:
        return None, str(res.message)
    return {xv[i]: int(round(float(v))) for i, v in zip(sel, res.x) if round(float(v)) > 0}, str(res.message)


def solve(jobs, chcs, zone_centroids, region, time_limit_s=10.0):
    """Returns {"outcome", "plan", "total", "info"} with plan keyed (date, chc_id, zone_id) -> [machines, ha],
    the same shape routing.simulate produces, or None if there is no usable solution."""
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_matrix
    except ImportError as e:  # pragma: no cover - depends on the environment
        print(f"  MILP skipped (scipy not available: {e})")
        return None

    as_of = d(region["as_of"])
    rate = region["machine_rate_ha_per_day"]
    max_km = region["max_service_km"]
    fleet_total = sum(c["machines"] for c in chcs)
    agg = aggregate_jobs(jobs, as_of)
    total = sum(j["ha"] for j in agg)
    zones = sorted({j["zone_id"] for j in agg})
    dist = {(c["chc_id"], z): haversine_km(c["lon"], c["lat"], *zone_centroids[z]) for c in chcs for z in zones}

    def day(x):
        return (x - as_of).days

    open_days, open_ha = defaultdict(set), defaultdict(float)
    for j in agg:
        for k in range(day(j["open"]), day(j["deadline"]) + 1):
            open_days[j["zone_id"]].add(k)
            open_ha[(j["zone_id"], k)] += j["ha"]

    xv = [(ci, z, k) for ci, c in enumerate(chcs) for z in zones if dist[(c["chc_id"], z)] <= max_km
          for k in sorted(open_days[z])]
    reachable = {z for _, z, _ in xv}
    mv = [(z, k) for z in zones if z in reachable for k in sorted(open_days[z])]
    yv = [(ji, k) for ji, j in enumerate(agg) for k in range(day(j["open"]), day(j["deadline"]) + 1)]
    nx, nm, ny = len(xv), len(mv), len(yv)
    n = nx + nm + ny
    info = {"n_jobs": len(jobs), "n_jobs_merged": len(agg), "n_int_vars": nm, "n_cont_vars": nx + ny,
            "time_limit_s": time_limit_s}
    if nx == 0 or ny == 0:
        info.update({"status": "nothing to dispatch", "optimal": True, "solve_s": 0.0, "n_constraints": 0,
                     "mip_gap": 0.0, "assign_status": "nothing to assign"})
        return {"outcome": _empty(total), "plan": {}, "total": total, "info": info}

    # ---- phase 1: machine-days per zone-day ----
    cost = np.zeros(n)
    for i, (ci, z, k) in enumerate(xv):
        cost[i] = EPS_KM * dist[(chcs[ci]["chc_id"], z)]
    cost[nx:nx + nm] = EPS_MACHINE
    cost[nx + nm:] = -1.0
    R = _Rows()
    for i, (ci, z, k) in enumerate(xv):
        R.add(("fleet", ci, k), -np.inf, chcs[ci]["machines"], i, 1.0)
        R.add(("link", z, k), 0.0, 0.0, i, 1.0)
    for i, (z, k) in enumerate(mv):
        R.add(("link", z, k), 0.0, 0.0, nx + i, -1.0)
        R.add(("cap", z, k), -np.inf, 0.0, nx + i, -rate)
    for i, (ji, k) in enumerate(yv):
        R.add(("cap", agg[ji]["zone_id"], k), -np.inf, 0.0, nx + nm + i, 1.0)   # unreachable zone-day: y <= 0
        R.add(("job", ji), -np.inf, agg[ji]["ha"], nx + nm + i, 1.0)
    m_rows = len(R.lo)
    A = coo_matrix((R.v, (R.r, R.c)), shape=(m_rows, n)).tocsr()
    upper = np.array([chcs[ci]["machines"] for ci, _, _ in xv]
                     + [min(fleet_total, math.ceil(open_ha[(z, k)] / rate)) for z, k in mv]
                     + [agg[ji]["ha"] for ji, _ in yv], dtype=float)
    integrality = np.concatenate([np.zeros(nx), np.ones(nm), np.zeros(ny)])

    t0 = time.perf_counter()
    res = milp(cost, constraints=LinearConstraint(A, np.array(R.lo), np.array(R.hi)), integrality=integrality,
               bounds=Bounds(np.zeros(n), upper),
               options={"time_limit": float(time_limit_s), "disp": False, "mip_rel_gap": MIP_REL_GAP})
    solve_s = round(time.perf_counter() - t0, 2)
    gap = getattr(res, "mip_gap", None)
    info.update({"status": str(res.message), "optimal": int(res.status) == 0, "solve_s": solve_s,
                 "n_constraints": m_rows, "mip_gap": None if gap is None else float(gap)})
    if res.x is None:
        print(f"  MILP: no solution in {solve_s}s ({res.message})")
        return None

    m_sol = [int(round(float(v))) for v in res.x[nx:nx + nm]]
    y_sol = [max(0.0, float(v)) for v in res.x[nx + nm:]]
    served_zd = defaultdict(float)
    for i, (ji, k) in enumerate(yv):
        served_zd[(agg[ji]["zone_id"], k)] += y_sol[i]
    # machines actually needed for the area cleared (drops machine-days the solution left idle)
    need = {}
    for i, (z, k) in enumerate(mv):
        req = min(m_sol[i], int(math.ceil(served_zd[(z, k)] / rate - 1e-9)))
        if req > 0:
            need[(z, k)] = req

    # ---- phase 2: which CHC sends each machine ----
    t1 = time.perf_counter()
    assign, assign_status = _assign_chcs(xv, need, chcs, dist, min(5.0, time_limit_s))
    if assign is None:  # should not happen (phase 1 proved feasibility); fall back to phase-1 split, nearest first
        assign = {}
        by_zd = defaultdict(list)
        for i, (ci, z, k) in enumerate(xv):
            if res.x[i] > 1e-6:
                by_zd[(z, k)].append([ci, int(math.ceil(float(res.x[i]) - 1e-6))])
        for (z, k), lst in by_zd.items():
            left = need.get((z, k), 0)
            for ci, cnt in sorted(lst, key=lambda e: dist[(chcs[e[0]]["chc_id"], z)]):
                use = min(cnt, left)
                left -= use
                if use > 0:
                    assign[(ci, z, k)] = use
        assign_status = f"fallback split ({assign_status})"
    info.update({"assign_status": assign_status, "assign_s": round(time.perf_counter() - t1, 3)})

    plan = defaultdict(lambda: [0, 0.0])  # (date, chc_id, zone_id) -> [machines, ha]
    for (ci, z, k), mch in assign.items():
        key = (s(as_of + timedelta(days=k)), chcs[ci]["chc_id"], z)
        plan[key][0] += mch
        plan[key][1] += served_zd[(z, k)] * mch / need[(z, k)]
    served = sum(y_sol)
    outcome = {"ha_served_in_window": round(served, 1), "ha_missed_window": round(total - served, 1),
               "pct_served": round(100 * served / total, 1) if total else 0.0}
    print(f"  MILP: {info['status']} in {solve_s}s | {nm} int + {nx + ny} real vars, {m_rows} constraints"
          f" | served {outcome['pct_served']}% | CHC assignment {assign_status.lower()}")
    return {"outcome": outcome, "plan": dict(plan), "total": total, "info": info}
