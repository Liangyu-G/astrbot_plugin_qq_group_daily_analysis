"""分析任务生命周期状态值对象。"""

from enum import Enum


class TaskStatus(str, Enum):
    """分析任务的持久化生命周期状态。

    新 Trace 直接以 ``RUNNING`` 状态创建。``PENDING`` 当前仅作保留状态，状态机不允许从中转移。
    ``COMPLETED`` 和 ``CANCELLED`` 为兼容旧版本写入的记录而保留。结果状态均为终态；
    哪些相同结果事件可以幂等重放，由状态机白名单定义。
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    WARNING = "warning"
    # 为兼容旧记录保留的终态；新任务使用 SUCCEEDED 表示正常完成。
    COMPLETED = "completed"
    FAILED = "failed"
    # 旧 WebUI 路径写入的状态；新取消事件统一映射为 ABORTED。
    CANCELLED = "cancelled"
    ABORTED = "aborted"
    SKIPPED = "skipped"
