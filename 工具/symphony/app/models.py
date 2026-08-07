from typing import Literal
from pydantic import BaseModel, Field, model_validator

class VideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1)
    refs: list[str] = Field(default_factory=list, max_length=9)
    mode: Literal["auto", "text", "image", "reference"] = "auto"
    frames: Literal["first", "first-last"] = "first"
    model: str | None = Field(default=None, min_length=1)
    duration: Literal[5, 10, 12, 15] = 5
    wait: bool = False
    timeout: int = Field(default=5400, ge=1, le=86400)
    backend: Literal["auto", "direct", "browser"] = "auto"
    main_account: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
    account: str | None = Field(default=None, min_length=1)

class WarmupRequest(BaseModel):
    """只上传参考图并登记进资产缓存，不提交生成、不扣积分（走 generate-video --prepare-only）。"""

    refs: list[str] = Field(min_length=1, max_length=9)
    model: str | None = "Dreamina Seedance 2.0"
    duration: Literal[5, 10, 12, 15] = 15
    prompt: str = "资产入库占位，不提交生成"
    timeout: int = Field(default=900, ge=1, le=3600)
    main_account: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
    account: str | None = Field(default=None, min_length=1)


class DownloadRequest(BaseModel):
    out: str | None = None
    allow_preview: bool = False
    timeout: int = Field(default=240, ge=1, le=3600)
    backend: Literal["auto", "direct", "browser"] = "auto"
    main_account: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
    account: str | None = Field(default=None, min_length=1)


class FlowImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1)
    project_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    reference_path: str | None = Field(default=None, min_length=1)
    uploaded_reference: str | None = Field(default=None, min_length=1)
    wait: bool = True
    prepare_only: bool = False
    timeout: int = Field(default=600, ge=30, le=3600)
    out: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_reference(self) -> "FlowImageGenerationRequest":
        if bool(self.reference_path) == bool(self.uploaded_reference):
            raise ValueError("pass exactly one of reference_path or uploaded_reference")
        if self.out and not self.wait:
            raise ValueError("out requires wait=true")
        if self.out and self.prepare_only:
            raise ValueError("out cannot be used with prepare_only=true")
        return self


class FlowImageDownloadRequest(BaseModel):
    project_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    out: str = Field(min_length=1)
    timeout: int = Field(default=120, ge=30, le=3600)

class LicenseActivationRequest(BaseModel):
    license: str = Field(min_length=40, max_length=8192)
