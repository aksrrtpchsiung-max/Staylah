每次回答前必须重新读AGENTS.md！！！！！

当前任务是：
开发，实现具体的代码

如何接入大模型：https://github.com/kenken64/ShowMeYourAgent-Starter-Kit/tree/main
我用的是langGrath开发！！！！

3a部分使用guru_search

硬性规则！！！不许违反
项目目录.md、模块设计.md和contracts_v0.py这三个文档是对你的约束，不可以和他们有冲突
文档之间冲突contracts_v0.py接口文档具有第一优先级

接口文档还包括：CONTRACT_CHANGES_FOR_BC.md、build_contract_examples.py、examples文件夹


不用保存测试代码
但是我要单元测试
比如A.py
里面有
def A():
    #具体代码

if __name__=="__main__":
    #单元测试

不许用虚拟数据测试
例如：我的3a功能，你在单元测试就是真实的2给3a的输入，返回的是3a给2的输出。
我的单元测试是至少三个符合真实输入的数据。