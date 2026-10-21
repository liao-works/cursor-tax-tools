"""
自定义异常类
用于更精确的错误处理和分类
"""


class UpdateError(Exception):
    """更新相关的基础异常"""
    pass


class NetworkError(UpdateError):
    """网络相关错误"""
    pass


class DatabaseError(UpdateError):
    """数据库相关错误"""
    pass


class MetadataError(UpdateError):
    """元数据相关错误"""
    pass


class IntegrityError(UpdateError):
    """完整性验证错误"""
    pass


class FileOperationError(UpdateError):
    """文件操作错误"""
    pass


class BackupError(UpdateError):
    """备份操作错误"""
    pass
