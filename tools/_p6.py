import pathlib, ast, json

eng = pathlib.Path("src/cashpilot/recon/engine.py"); s = eng.read_text()
s = s.replace('''        self._settlement_by_utr = {s.payout_utr: s.settlement_id for s in ds.settlements if s.payout_utr}''',
'''        # "today" for a hand-built or manifest-less dataset is the last line we can see; without this
        # fallback the aged-receivables pass below compares dates against None and the run dies.
        self.as_of = ds.as_of or (max((ln.txn_date for ln in ds.lines), default=date.today()) if ds.lines else date.today())
        self._settlement_by_utr = {s.payout_utr: s.settlement_id for s in ds.settlements if s.payout_utr}''')
s = s.replace('''                for cand in self._settlement_by_id:
                    if cand in text:''','''                for cand in self._settlement_by_id:
                    # narrations are upper/mixed case unpredictably; "SETL-1" in a lowercased string
                    # must still find the settlement it names
                    if cand.lower() in text:''')
s = s.replace('''            if d.kind == "AR" and d.outstanding_paise > 0 and d.due_date < self.dataset.as_of:''',
              '''            if d.kind == "AR" and d.outstanding_paise > 0 and d.due_date < self.as_of:''')
s = s.replace('''                    "high" if (self.dataset.as_of - d.due_date).days > 45 else "medium",''',
              '''                    "high" if (self.as_of - d.due_date).days > 45 else "medium",''')
s = s.replace('''                    f"Invoice {d.number} for {d.counterparty} is {(self.dataset.as_of - d.due_date).days} days past due with no matching receipt.",''',
              '''                    f"Invoice {d.number} for {d.counterparty} is {(self.as_of - d.due_date).days} days past due with no matching receipt.",''')
if "    as_of: date = field(init=False)" not in s:
    s = s.replace('''    _settlement_by_utr: dict[str, str] = field(init=False, default_factory=dict)''',
'''    as_of: date = field(init=False, default=None)  # type: ignore[assignment]
    _settlement_by_utr: dict[str, str] = field(init=False, default_factory=dict)''')
eng.write_text(s); ast.parse(s)

# patterns: the old default required a 3-6 digit tail, so "INV-4711" and "VB-991122" were never
# extracted and their lines fell through to the fuzzy tiers.
pat = r"\b(?:INV|SINV|SALES|RT|PO|BILL|VB|SUP)[-/]?\d{4}(?:[-/]?\d{1,6})?\b"
cfgp = pathlib.Path("config/recon_rules.json"); cfg = json.loads(cfgp.read_text())
cfg["invoice_number_patterns"] = [pat]
cfgp.write_text(json.dumps(cfg, indent=2) + "\n")
cf = pathlib.Path("src/cashpilot/config.py"); c = cf.read_text()
i = c.index('"invoice_number_patterns": [')
j = c.index("],", i) + 1
c = c[:i] + '"invoice_number_patterns": [\n        "%s",' % pat.replace("\\", "\\\\") + c[j:]
cf.write_text(c); ast.parse(c)
print("patterns ->", json.loads(cfgp.read_text())["invoice_number_patterns"])
import importlib, sys
sys.path.insert(0, str(pathlib.Path("src").resolve()))
from cashpilot.config import load_settings
st = load_settings()
print("default settings patterns:", st.rules["invoice_number_patterns"][:1])
from cashpilot.norm import invoice_tokens
for n in ["NEFT CR ACME 450000 REF INV-4711 CLOSING", "NEFT CR ACME REF INV-2026-4711", "BILL PAYMENT VB-991122", "NEFT DR M/s ACME REF BILL-471122"]:
    print(repr(n), invoice_tokens(n, st.rules["invoice_number_patterns"]))
