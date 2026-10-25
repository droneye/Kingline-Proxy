import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Credentials(StrictModel):
    password: SecretStr | None = None
    private_key: SecretStr | None = None
    passphrase: SecretStr | None = None

    @model_validator(mode="after")
    def one_method(self):
        if self.password and self.private_key:
            raise ValueError("Choose password or private_key, not both")
        for value in (self.password, self.private_key, self.passphrase):
            if value and not 1 <= len(value.get_secret_value()) <= 32768:
                raise ValueError("Invalid credential length")
        return self

    def reveal(self):
        return {k: v.get_secret_value() for k, v in self.__dict__.items() if v is not None}


class AddNode(StrictModel):
    ip: str
    name: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]+$")
    label: str | None = Field(default=None, min_length=1, max_length=60)
    display_name: str | None = Field(default=None, min_length=1, max_length=50)
    flag_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    quota_tb: float = Field(gt=0, le=100000)
    speed_mbps: float = Field(gt=0, le=1000000)
    smart_enabled: bool = True
    ru_enabled: bool = True
    reset_day: int = Field(default=19, ge=1, le=31)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    host_key_sha256: str | None = Field(default=None, pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    credentials: Credentials = Field(default_factory=Credentials)

    @field_validator("ip")
    @classmethod
    def public_v4(cls, value):
        address = ipaddress.IPv4Address(value)
        if not address.is_global:
            raise ValueError("A public IPv4 address is required")
        return str(address)

    @field_validator("label", "display_name")
    @classmethod
    def clean_label(cls, value):
        if value is None:
            return value
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Control characters are forbidden")
        if not value.strip():
            raise ValueError("A visible name is required")
        return value.strip()

    @model_validator(mode="after")
    def presentation(self):
        self.name = self.name or self.country + '-' + self.ip.replace('.', '-')
        if self.display_name:
            self.flag_code = self.flag_code or self.country
            flag = ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in self.flag_code)
            label = flag + ' ' + self.display_name
            if self.label is not None and self.label not in (label, self.display_name):
                raise ValueError("label conflicts with display_name and flag_code")
            self.label = self.label or label
        elif self.label:
            # Preserve old API/CLI requests that already contain a complete label.
            prefix = self.label[:2]
            has_flag = len(prefix) == 2 and all(0x1F1E6 <= ord(c) <= 0x1F1FF for c in prefix)
            self.display_name = self.label[2:].strip() if has_flag else self.label
            embedded = ''.join(chr(ord(c) - 0x1F1E6 + ord('A')) for c in prefix) if has_flag else None
            if self.flag_code and embedded and self.flag_code != embedded:
                raise ValueError("flag_code conflicts with the flag in label")
            if self.flag_code and not has_flag:
                self.label = ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in self.flag_code) + ' ' + self.label
            self.flag_code = self.flag_code or embedded or self.country
        else:
            raise ValueError("display_name or label is required")
        return self


class Capacity(StrictModel):
    quota_tb: float | None = Field(default=None, gt=0, le=100000)
    speed_mbps: float | None = Field(default=None, gt=0, le=1000000)
    reset_day: int | None = Field(default=None, ge=1, le=31)
    maintenance: bool | None = None
    smart_enabled: bool | None = None
    ru_enabled: bool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not any(v is not None for v in self.__dict__.values()):
            raise ValueError("No changes supplied")
        return self


class RemoveNode(StrictModel):
    name: str = Field(min_length=3, max_length=30)
    ip: str


class RUReceiver(StrictModel):
    ip: str
    ssh_port: int = Field(default=22,ge=1,le=65535)
    display_name: str = Field(default='Российский выход',min_length=1,max_length=50)
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    credentials: Credentials = Field(default_factory=Credentials)

    @field_validator('ip')
    @classmethod
    def public_ip(cls,value):return AddNode.public_v4(value)

    @field_validator('display_name')
    @classmethod
    def visible_name(cls,value):return AddNode.clean_label(value)

    @field_validator('ssh_port')
    @classmethod
    def free_service_port(cls,value):
        if value in {2222,15443}:raise ValueError('SSH port conflicts with the private node service')
        return value


class ProbeReport(StrictModel):
    node_uuid: str = Field(pattern=r"^[a-f0-9-]{36}$")
    sequence: int = Field(ge=1, le=9223372036854775807)
    vpn_ok: bool
    latency_ms: float | None = Field(default=None, ge=0, le=60000)
    error: Literal["connect", "tls", "http", "timeout", "other"] | None = None


class NodeVisibility(StrictModel):
    uuid: str = Field(pattern=r'^[a-f0-9-]{36}$')
    hidden: bool
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')


class ReviewAction(StrictModel):
    review_id: int = Field(ge=1)
    operation: Literal['check','keep','hide','restore','via']
    parent: str | None = Field(default=None,pattern=r'^[a-f0-9-]{36}$')
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')


class ReviewCheck(StrictModel):
    node: str | None = Field(default=None,pattern=r'^[a-f0-9-]{36}$')
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')


class InstallationAction(StrictModel):
    job_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    operation: Literal['check', 'test-key', 'approve', 'cancel']
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    revision: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class VPNClient(StrictModel):
    sni: str = Field(min_length=1, max_length=253, pattern=r'^[A-Za-z0-9.-]+$')
    fingerprint: Literal['firefox', 'chrome', 'safari', 'edge', 'randomized']


class VPNDraft(StrictModel):
    profile_uuid: str = Field(pattern=r'^[a-f0-9-]{36}$')
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    scope: Literal['profile', 'selected', 'all']
    nodes: list[str] = Field(default_factory=list, max_length=100)
    config: dict
    client: VPNClient
    note: str = Field(default='', max_length=300)


class VPNApply(StrictModel):
    id: str = Field(pattern=r'^[a-f0-9-]{36}$')


class VPNRollback(VPNApply):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class NodeChange(StrictModel):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')


class NodeConnection(NodeChange):
    uuid: str = Field(pattern=r'^[a-f0-9-]{36}$')
    sni: str = Field(min_length=3, max_length=253, pattern=r'^[A-Za-z0-9.-]+$')
    target: str = Field(min_length=3, max_length=260)

    @field_validator('target')
    @classmethod
    def valid_target(cls, value):
        from urllib.parse import urlsplit
        parsed = urlsplit('//'+value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.port:
            raise ValueError('A hostname and port are required')
        if not re.fullmatch(r'[A-Za-z0-9.:-]+', parsed.hostname): raise ValueError('Invalid target')
        try: address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname) or '.' not in parsed.hostname or re.fullmatch(r'[0-9.]+', parsed.hostname):
                raise ValueError('Invalid public hostname')
        else:
            if not address.is_global: raise ValueError('Public target required')
        return value


class NodeRoutes(NodeChange):
    via: dict[str, str | None] = Field(max_length=100)

    @field_validator('via')
    @classmethod
    def valid_nodes(cls, value):
        if any(not re.fullmatch(r'[a-f0-9-]{36}', v) for k,p in value.items() for v in (k,p) if v is not None):
            raise ValueError('Invalid node')
        return value


class NodeOrder(NodeChange):
    hosts: list[str] = Field(min_length=1, max_length=200)

    @field_validator('hosts')
    @classmethod
    def valid_hosts(cls, value):
        if any(not re.fullmatch(r'[a-f0-9-]{36}', v) for v in value): raise ValueError('Invalid host')
        return value
