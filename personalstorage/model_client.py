"""Reserved model-provider protocol. Deliberately contains no SDK or network code."""
from typing import Protocol

class ReviewProvider(Protocol):
    def review(self,request:dict)->dict: ...

class DisabledProvider:
    def review(self,request:dict)->dict:
        raise NotImplementedError('模型接入已按要求禁用；使用离线review-export/review-import接口')
