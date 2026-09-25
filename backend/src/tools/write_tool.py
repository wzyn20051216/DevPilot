###写文件
import os
import tempfile
from pathlib import Path
from .file_tool import resolve_safe_path

#保护文件集合
PROTECTED_FILES={
    ".env",
    ".env.local",
    ".env.production",
}

def write_file(
     repo_path:str,
     file_path:str,
     content:str,
     max_chars:int =200_000,
    )-> dict[str,object]:
    """
    安全修改代码仓库中**已存在**的文本文件，使用原子写入避免文件损坏。

    Args:
        repo_path: 代码仓库根目录路径
        file_path: 相对于仓库根目录的目标文件相对路径
        content: 需要写入覆盖的新文件文本内容
        max_chars: 单次写入最大字符上限，防止超大文件写入

    Returns:
        dict: 返回执行结果字典，包含文件路径、是否变更、提示信息等

    Raises:
        ValueError: 修改受保护文件、.git目录、目标不是文件、内容超长时抛出
        FileNotFoundError: 文件不存在时抛出（本函数只允许修改已有文件）
    """
    #解析得到安全的绝对路径，做路径月结防护，防止跳出目录
    path=resolve_safe_path(Path(repo_path),relative_path=file_path)
    #禁止修改敏感文件
    if path.name in PROTECTED_FILES:
        raise ValueError(f"禁止操作{path.name}{repo_path}")
    #禁止操作git版本控制目录
    if ".git" in path.parts:
        raise ValueError(
            "禁止修改 .git 目录"
        )
    #检验目标路径是普通文件 不是文件夹
    if not path.is_file():
        raise ValueError(f"{file_path}不是目标文件")
    #检验写入内容长度 限制最大字节数
    if len(content)>max_chars:
        raise ValueError(f"{file_path}超出字符属于")
    #读取文件原始内容 编码忽略非法字符
    old_content=path.read_text(encoding="utf-8",errors="ignore")
    # 新内容和旧内容完全一致，无需写入，直接返回无变更结果
    if old_content == content:
        return {
            "file_path": file_path,
            "changed": False,
            "message": "文件内容没有变化",
        }
    # ======================
    # 原子写入逻辑：先写同目录临时文件，全部写完再替换原文件
    # 好处：防止程序中途崩溃，造成原文件截断损坏
    # ======================
    # 在目标文件同级目录创建临时文件，生成文件描述符+临时文件路径
    fd,temp_path=tempfile.mkstemp(dir=str(path.parent),prefix=".devpilot_",suffix=".tmp") #pre是前缀 suf是后缀
    try:
        #通过文件描述符打开临时文件，写入新内容
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as f:
            f.write(content)
        # 原子替换：把临时文件直接覆盖替换真正的目标文件
        os.replace(
            temp_path,
            path,
        )
    except Exception:
        # 发生异常：清理残留临时文件，再向上抛出异常
        if os.path.exists(temp_path):
            os.remove(temp_path)#需要手动删除
        raise

    # 写入成功，返回变更信息
    return {
        "file_path": file_path,
        "changed": True,
        "old_chars": len(old_content),
        "new_chars": len(content),
        "message": "文件修改成功",
    }