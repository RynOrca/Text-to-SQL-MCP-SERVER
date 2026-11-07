"""问数（AskData）· 模块化数据问答服务。

包结构（每一层都能被替换，见 docs/项目方案.md 第 6 节）：

    core/        配置、接口契约、链路追踪
    connectors/  数据库连接与只读沙箱
    schema/      库结构内省、数据字典、外键图
    retrieval/   表级召回、融合、重排、图扩展
    generation/  提示词组装、模型调用、报错重写
    execution/   语句校验与执行
    evaluation/  执行准确率、检索召回率、失败归因
    mcp_server/  对外暴露的工具
"""

__version__ = "0.1.0"
__app_name__ = "问数"
