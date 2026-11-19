# F06：受控自定义 Python 插件 AST 门禁与步数预算逃逸防线

- **触发场景**：构建自托管/本地受控 Python 插件热加载、表达式求值、策略或规则脚本沙盒（AST 白名单 + `exec` 受限内建 + `sys.settrace` 步数预算）。
- **典型错的样子（浅补丁反模式）**：
  1. **只按模块名放行标准库**：放行 `dataclasses`、`statistics` 等整模块，未按符号级过滤，导致 `from dataclasses import sys` 或 `from statistics import random` 借标准库重导出偷渡受限模块；
  2. **导出可变配置字典给插件**：将运行时使用的白名单 `dict` 暴露在公共入口 `__all__` 中，插件经两次干跑或同批两个文件即可先篡改白名单再导入 `subprocess`；
  3. **漏判 Python 3.10+ 结构模式匹配与别名化 `type()`**：仅拦截 `ast.Attribute` 与 3 参数 `type(...)` 调用，被 `match x: case Cls(__mro__=m):`（`ast.MatchClass.kwd_attrs`）或 `t = type; t("X", (), {})` 直接穿透；
  4. **步数预算抛普通 `Exception` 被插件吞掉**：`sys.settrace` 回调抛 `RuntimeError`/`Exception` 子类时，插件写 `while True: try: ... except Exception: pass` 会截获异常并触发 CPython 自动卸载线程 `settrace`，导致工作线程永久挂死。
- **治本做法**：
  1. **只读符号级双端白名单**：标准库与宿主领域导出均收紧为只读 `MappingProxyType` / `frozenset` 精确符号表（仅暴露纯数据模型与无副作用纯函数），AST 校验与运行时 `__import__` 钩子共享同一份不可变表，并封禁整包导入与受限 `asname` 别名；
  2. **补齐新语法与动态造类防线**：AST 禁止 `MatchClass`、裸 `except:`、`BaseException` 及解包 `type(*args)`，运行时内建表用单参包装函数 `lambda obj: type(obj)` 替代原生 `type`；
  3. **`BaseException` 不可吞噬步数预算与独立双实例烟测**：`sys.settrace` 超限时抛私有 `BaseException` 子类（外层边界转为业务异常并恢复 `prev_trace`）；加载与干跑在工作线程执行、`sys.modules` 用唯一随机后缀隔离，并正/逆序双独立实例烟测拦截单例与隐藏可变状态。
- **必配测试**：用 `from dataclasses import sys`、修改只读白名单、`match/case Cls(__mro__=m)`、`while True: try: ... except Exception: pass`、别名 `t = type; t(...)` 与并发多线程干跑作为常驻回归测试，全部必须在毫秒级被稳定拦截且零线程泄漏。
