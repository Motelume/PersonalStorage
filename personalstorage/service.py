"""Stable application boundary shared by the CLI and the loopback web adapter."""
from pathlib import Path
from .state import State
from .policy import Policy
from .planner import build_plan
from .executor import execute
from .recovery import recover
from . import reports
from .paths import canonical_root

class Application:
    def __init__(self,root,profile=None):
        self.root=canonical_root(root); self.policy=Policy(self.root); self.state=State(self.root,profile)
    def plan(self,**options): return build_plan(self.root,self.state,self.policy,**options)
    def execute(self,plan_id): return execute(self.root,self.state,self.policy,plan_id)
    def recover(self): return recover(self.root,self.state,self.policy)
    def status(self): return reports.status(self.state)
    def pending(self): return reports.pending(self.state)
    def search(self,query): return reports.search(self.state,query)
    def pause(self,paused=True): self.state.set('paused',bool(paused)); return {'paused':bool(paused)}
    def export_review(self,target,with_content=False,limit=30):
        from .review import export_tasks
        return export_tasks(self.root,self.state,self.policy,target,with_content,limit)
    def import_review(self,payload):
        from .review import import_results
        return import_results(self.root,self.state,self.policy,payload)
    def check(self,full_hash=False):
        from .maintenance import reconcile
        return reconcile(self.root,self.state,self.policy,full_hash)
    def snapshot(self,target):
        from .snapshot import management_snapshot
        return management_snapshot(self.root,self.state,self.policy,target)
    def close(self): self.state.close()
