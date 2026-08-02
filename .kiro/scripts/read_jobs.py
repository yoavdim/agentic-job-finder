#!/usr/bin/env python3
"""Read full LinkedIn job listings by id, in PARALLEL batches (pool of 5).
Targets the sections that matter: Responsibilities / "What you'll do" and
Qualifications / Requirements / "What you bring" (names vary), plus level/experience
signals. Usage: read_jobs.py <id1> <id2> ..."""
import time,sys,re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import tab_share as TS

POOL=5
def post(p,b,t=35):
    resp, err = TS.post_raw(p,b,timeout=t)
    return {"error": err} if err else resp

def chunks(l,n):
    for i in range(0,len(l),n): yield l[i:i+n]

# section headers we care about (case-insensitive), and where each realistically ends
RESP = ["what you'll do","what you will do","responsibilities","the role","your role","in this role","day to day","day-to-day","about the role"]
REQ  = ["minimum qualification","basic qualification","required qualification","qualifications","requirements","what you bring","what you'll need","what you will need","who you are","skills and experience","required skills","you have","must have","we're looking for","we are looking for"]
EXP  = re.compile(r'(\d+)\+?\s*(?:-\s*\d+\s*)?years?', re.I)

def section(text, heads, span=1100):
    low=text.lower()
    best=None
    for h in heads:
        i=low.find(h)
        if i>=0 and (best is None or i<best): best=i
    if best is None: return ""
    return text[best:best+span]

def clean(s): return re.sub(r'\n{2,}','\n',s).strip()

ids=sys.argv[1:]
for batch in chunks(ids,POOL):
    tabmap={}
    for jid in batch:
        r=post("/open",{"url":"https://www.linkedin.com/jobs/view/%s/"%jid,"groupName":"Scratch"})
        tabmap[jid]=r.get("tabId")
    time.sleep(9)  # let the description body render
    for jid in batch:
        d=post("/extract",{"tabId":tabmap[jid]})
        t=d.get("text","") or ""
        low=t.lower()
        removed=any(m in low for m in ["no longer accepting","no longer available","this job is no longer"])
        title=(d.get("title","") or "").replace(" | LinkedIn","")
        # meta line (location · posted · applicants) sits right after the title in the text
        remote = "remote" in low[:1500]
        yrs=sorted(set(int(m.group(1)) for m in EXP.finditer(t)))
        resp=section(t,RESP); req=section(t,REQ)
        print("\n===== %s | %s | removed=%s | years-mentioned=%s =====" % (jid,title,removed,yrs or "none"))
        if resp: print("· WHAT YOU'LL DO:\n"+clean(resp)[:700])
        if req:  print("· REQUIREMENTS:\n"+clean(req)[:800])
        if not resp and not req:
            print("(sections not found — body may not have rendered) head:", clean(t[:200]))
    tids=[v for v in tabmap.values() if v]
    if tids:
        post("/close",{"tabId":tids,"expectHost":"www.linkedin.com","expectGroup":"Scratch"})
