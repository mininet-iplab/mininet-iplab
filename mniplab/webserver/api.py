"""REST API endpoints for mnIPLab web server."""

import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
import unicodedata
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple, TypeVar, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, status, Response, Cookie, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from mniplab.errors import LabUnavailableError

from . import auth
from .auth import (
    get_current_user_with_role,
    require_role,
    verify_credentials,
    create_session,
    destroy_session,
    destroy_previous_session,
    destroy_user_sessions,
    expire_idle_sessions,
    list_sessions,
    make_room_for_session,
    security,
)
from .config import settings
from .catalog import AREAS
from .groups import catalog_ids
from .session import registry
from .logins import LoginRefused, LoginStore, LoginTaken
from .server_settings import LimitRefused, SettingsStore
from .repositories import (
    LayoutRepository,
    GuideRepository,
    load_labs_manifest,
    normalize,
    resolve_examples_dir,
)
from .authoring import (
    ROUTER_PROTOCOLS,
    AuthoringRepository,
    DraftConflictError,
    DraftNotFoundError,
    DraftValidationError,
    authored_labs_root,
    shipped_definition,
    has_code_section,
)
from .authoring_keep import apply_keep, review_keep, snapshot_preview
from .authoring_record import (
    RecordError, code_section_drawing, record_script, running_drawing,
)
from .authoring_python import (
    code_section_first_line, generate_python, generated_python_parts, package_archive,
    script_slug,
)


# Request/Response models
class StartLabRequest(BaseModel):
    """The Lab Example to start, by its catalog id.

    ``group`` lets the Instructor start it for another Group in Multi-Lab
    Mode; omitted, the Lab is the caller's own.  ``clean`` is "start
    clean": the Group's copy of the Lab Example's Persistent Config is
    discarded first, so the Lab starts from the seed.
    """

    example_id: str
    group: Optional[str] = None
    clean: bool = False


class ServerSettingsRequest(BaseModel):
    """Limits to change in Server settings, by name.

    Values are checked by the Settings Store, so a refusal can name the
    field the dialog shows.
    """

    limits: Dict[str, Any]


class AddLoginRequest(BaseModel):
    """A Learner login to add in Server settings; its name is checked there."""

    name: str


class CloseTerminalRequest(BaseModel):
    """The Terminal an Instructor is force-closing."""

    terminal_id: str


class SessionNicknameRequest(BaseModel):
    """A nickname change for the authenticated caller's own Session."""

    model_config = ConfigDict(extra="forbid")
    nickname: StrictStr = Field(min_length=1, max_length=40)

    @field_validator("nickname", mode="before")
    @classmethod
    def readable_nickname(cls, value: Any) -> Any:
        """Trim a name and reject control characters before validation."""
        if isinstance(value, str):
            if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
                raise ValueError("Use a nickname without control characters")
            return value.strip()
        return value


class ConfigResetRequest(BaseModel):
    """A Lab Example whose Persistent Config copies are reset to the seed.

    ``group`` names one Group; omitted, every Group holding a copy.
    """

    example_id: str
    group: Optional[str] = None


class AssignmentRequest(BaseModel):
    """The Lab Examples a Group may start, by catalog id."""

    example_ids: List[str]


class SaveAuthoringDraftRequest(BaseModel):
    """An edited definition with the revision it was opened from."""

    revision: int = Field(ge=0)
    document: Dict[str, Any]


class PutAuthoringFileRequest(BaseModel):
    """One additional lesson file and the draft revision it was edited from."""

    model_config = ConfigDict(extra="forbid")

    revision: StrictInt = Field(ge=0)
    path: StrictStr
    file: Dict[str, Any]


class CreateAuthoringDraftRequest(BaseModel):
    """Create an empty draft, copy a shared draft, or Copy or Revise a Lab Example."""

    model_config = ConfigDict(extra="forbid")

    source_id: Optional[str] = None
    example_id: Optional[str] = None
    revise: bool = False
    source: Optional[Literal["recipe", "canvas"]] = None


class AuthoringRevisionRequest(BaseModel):
    """The saved draft revision to preview or publish."""

    revision: int = Field(ge=0)
    replace_key: Optional[str] = None
    mode: str = "save_as_new"


class ContinuePythonRequest(BaseModel):
    """The Canvas revision to convert into an independent Python draft."""

    model_config = ConfigDict(extra="forbid")

    revision: StrictInt = Field(ge=0)


class ForceLogoutRequest(BaseModel):
    """The Session an Instructor is ending on someone else's behalf."""

    session_id: str


class SaveLayoutRequest(BaseModel):
    example: Optional[str] = None
    positions: Dict[str, Any]


class PingRequest(BaseModel):
    source: str
    destination: str
    count: int = Field(default=4, ge=1, le=100)
    ipv6: bool = False


class TracerouteRequest(BaseModel):
    source: str
    destination: str
    ipv6: bool = False
    resolve: bool = False


class IperfRequest(BaseModel):
    server: str
    client: str


class Iperf3Request(BaseModel):
    server: str
    client: str
    seconds: int = 10
    protocol: str = 'tcp'
    json_output: bool = False


class LinkStatusRequest(BaseModel):
    node1: str
    node2: str


class LinkConfigureRequest(BaseModel):
    node1: str
    node2: str
    delay: Optional[str] = None
    jitter: Optional[str] = None
    loss: Optional[str] = None
    bandwidth: Optional[str] = None
    scheduler: str = "hfsc"


class AnnounceRequest(BaseModel):
    """A route a Learner announces from a Speaker, in the CLI's field set.

    ``as_path`` and ``med`` admit strings so a mistyped value reaches the
    route grammar, which refuses it naming the word the way the CLI does,
    rather than being turned away here with a generic type error.
    """

    prefix: Optional[str] = None
    next_hop: Optional[str] = None
    as_path: List[Union[int, str]] = Field(default_factory=list)
    community: List[str] = Field(default_factory=list)
    large_community: List[str] = Field(default_factory=list)
    med: Union[int, str, None] = None
    neighbor: Optional[str] = None


class GenerateRequest(BaseModel):
    """A burst of generated routes a Learner asks a Speaker for.

    Numbers admit strings for the same reason ``AnnounceRequest`` does: the
    grammar names a bad value the way the CLI does.
    """

    count: Union[int, str, None] = None
    neighbor: Optional[str] = None
    sub_prefixes: Union[int, str, None] = None
    prefix_seed: Union[int, str, None] = None
    as_path_seed: Union[int, str, None] = None


class WithdrawRequest(BaseModel):
    """A prefix a Learner withdraws, or ``all`` for a Neighbor's whole RIB."""

    prefix: Optional[str] = None
    all: bool = False
    neighbor: Optional[str] = None


class ReservationRequest(BaseModel):
    """One Reservation a Learner is authoring, pinning a client to an address.

    Exactly one of ``mac``/``duid`` must be set — a hardware address for an
    IPv4 Reservation, either identifier for an IPv6 one (DHCPv6's baseline is
    the DUID). The family-aware rejection of both or neither happens in
    :class:`mniplab.dhcp.pool.validate_reservation`, so the Learner receives
    the same explanation a Python author of a Lab Example would.
    """

    mac: str = ""
    duid: str = ""
    ip: str
    hostname: str = ""


class PoolUpdateRequest(BaseModel):
    """The Pool a Learner is authoring for a Subnet."""

    cidr: str = ""
    start: str
    end: str
    lease_time: Optional[int] = None
    preferred_lifetime: Optional[int] = None
    router: str = ""
    dns_server: str = ""
    reservations: list[ReservationRequest] = Field(default_factory=list)


# Create router
router = APIRouter(prefix="/api", tags=["api"])


async def require_instructor_user(user: dict = Depends(get_current_user_with_role)) -> dict:
    """Authorize Instructor authoring without dispatching a sync dependency."""
    if user["role"] != "instructor":
        raise HTTPException(status_code=403, detail="Instructor role required")
    return user


def authoring_repository() -> AuthoringRepository:
    """Build the repository facade for the configured authored-labs root."""
    return AuthoringRepository(authored_labs_root())


#: What a draft response adds to the stored draft, never saved with it.
DRAFT_RESPONSE_FIELDS = ("preview_valid", "can_update", "python_error")


def authoring_draft_response(repository: AuthoringRepository, draft: Dict[str, Any]) -> dict:
    """Add a draft's :data:`DRAFT_RESPONSE_FIELDS` to it for a response.

    Those are its current-preview evidence, whether it can update its Lab
    Example, and a Python draft's compile error.
    """
    catalog_id = draft["catalog"]["id"]
    response = {**draft, "preview_valid": repository.preview_status(draft)["current"],
                "can_update": bool(catalog_id) and repository.published_example(catalog_id) is not None}
    if draft.get("kind") == "python":
        response["python_error"] = repository.python_error(draft)
    return response


def _authoring_validation_response(exc: ValueError) -> JSONResponse:
    """Build an HTTP 422 response preserving structured DraftValidationError targets."""
    content: Dict[str, Any] = {"detail": str(exc)}
    if isinstance(exc, DraftValidationError):
        content["target"] = exc.target
    return JSONResponse(status_code=422, content=content)


@router.get("/authoring/drafts")
async def list_authoring_drafts(
    _user: dict = Depends(require_instructor_user),
) -> dict:
    """List the shared Lab Example Drafts (Instructor only)."""
    try:
        repository = authoring_repository()
        return {"drafts": [authoring_draft_response(repository, draft) for draft in repository.list_drafts()]}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/authoring/protocols")
async def list_authoring_protocols(
    _user: dict = Depends(require_instructor_user),
) -> dict:
    """List the Protocols a Router in a draft may enable (Instructor only)."""
    return {"protocols": list(ROUTER_PROTOCOLS)}


@router.post("/authoring/drafts", status_code=201)
async def create_authoring_draft(
    request: CreateAuthoringDraftRequest,
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Create a shared draft or copy another existing draft (Instructor only)."""
    try:
        repository = authoring_repository()
        if request.source_id and request.example_id:
            raise ValueError("Choose a draft or a Lab Example to copy")
        if request.source and (not request.example_id or request.revise):
            raise ValueError("Choose a shipped Lab Example to select recipe or Canvas source")
        if request.example_id:
            example = catalog_entry(request.example_id)
            if example is None:
                raise DraftNotFoundError(request.example_id)
            # Authored examples retain their authoritative source mode and
            # complete saved files for both Copy and Revise.
            if request.source and example.get("authored"):
                raise ValueError("Source selection is only available for shipped Lab Examples")
            if request.source == "recipe":
                from mniplab.webserver.authoring_copy import copy_shipped_recipe

                created = copy_shipped_recipe(repository, request.example_id, resolve_examples_dir())
                return authoring_draft_response(repository, created)
            if example.get("authored"):
                with open(repository.published_file(request.example_id, "definition.json"),
                          encoding="utf-8") as source:
                    definition = json.load(source)
            elif request.revise:
                raise ValueError("A shipped Lab Example cannot be revised; Copy it instead")
            else:
                definition = shipped_definition(request.example_id, resolve_examples_dir())
            if not request.revise:
                definition["catalog"] = {**definition["catalog"], "id": None}
            created = repository.create_from_example(definition)
        else:
            created = repository.create(request.source_id)
        return authoring_draft_response(repository, created)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.post("/authoring/drafts/upload", status_code=201)
async def upload_authoring_script(
    file: UploadFile = File(...),
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Make a new Python draft from one uploaded ``.py`` script (Instructor only)."""
    repository = authoring_repository()
    try:
        data = await file.read(repository.UPLOAD_LIMIT + 1)
        return authoring_draft_response(repository, repository.upload(file.filename or "", data))
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.put("/authoring/drafts/{draft_id}/script")
async def replace_authoring_script(
    draft_id: str,
    revision: int = Form(..., ge=0),
    file: UploadFile = File(...),
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Replace only an existing Python draft's script (Instructor only).

    Multipart fields are ``revision`` (the current draft revision) and
    ``file`` (one UTF-8 ``.py`` script no larger than the upload limit).
    """
    repository = authoring_repository()
    try:
        data = await file.read(repository.UPLOAD_LIMIT + 1)
        updated = repository.replace_script(draft_id, revision, file.filename or "", data)
        return authoring_draft_response(repository, updated)
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.post("/authoring/drafts/import", status_code=201)
async def import_authoring_package(
    file: UploadFile = File(...),
    source: Optional[Literal["python"]] = Query(default=None),
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Import a validated native lesson as a new unpublished draft."""
    from .authoring_package import (
        MAX_COMPRESSED_BYTES, PythonImportChoiceRequired, import_package,
    )

    repository = authoring_repository()
    try:
        data = await file.read(MAX_COMPRESSED_BYTES + 1)
        return authoring_draft_response(repository, import_package(repository, data, source))
    except PythonImportChoiceRequired as exc:
        return JSONResponse(status_code=409, content={
            "detail": str(exc), "code": "python_import_choice_required",
            "source_mode": "canvas", "choices": ["python"],
            "changed_files": exc.changed_files,
        })
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.get("/authoring/drafts/{draft_id}")
async def get_authoring_draft(
    draft_id: str,
    _user: dict = Depends(require_instructor_user),
) -> dict:
    """Read one shared Lab Example Draft (Instructor only)."""
    try:
        repository = authoring_repository()
        return authoring_draft_response(repository, repository.read(draft_id))
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/authoring/drafts/{draft_id}/continue-in-python", status_code=201)
async def continue_authoring_draft_in_python(
    draft_id: str,
    request: ContinuePythonRequest,
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Create a revision-checked Python continuation (Instructor only)."""
    repository = authoring_repository()
    try:
        created = repository.continue_in_python(draft_id, request.revision)
        return authoring_draft_response(repository, created)
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.put("/authoring/drafts/{draft_id}")
async def save_authoring_draft(
    draft_id: str,
    request: SaveAuthoringDraftRequest,
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Save one revision-checked shared Lab Example Draft (Instructor only)."""
    try:
        repository = authoring_repository()
        document = {key: value for key, value in request.document.items()
                    if key not in DRAFT_RESPONSE_FIELDS}
        return authoring_draft_response(
            repository, repository.save(draft_id, request.revision, document)
        )
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.delete("/authoring/drafts/{draft_id}", status_code=204)
async def delete_authoring_draft(
    draft_id: str,
    revision: int,
    _user: dict = Depends(require_instructor_user),
) -> None:
    """Delete one revision-checked shared Lab Example Draft (Instructor only)."""
    try:
        authoring_repository().delete(draft_id, revision)
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/authoring/drafts/{draft_id}/files")
async def list_authoring_files(
    draft_id: str, _user: dict = Depends(require_instructor_user),
) -> Any:
    """List saved supporting file metadata (Instructor only)."""
    try:
        return authoring_repository().list_files(draft_id)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.get("/authoring/drafts/{draft_id}/file")
async def read_authoring_file(
    draft_id: str, path: str, _user: dict = Depends(require_instructor_user),
) -> Any:
    """Download exact supporting bytes as an attachment (Instructor only)."""
    try:
        data = authoring_repository().read_file(draft_id, path)
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="{path.rsplit("/", 1)[-1]}"',
            "X-Content-Type-Options": "nosniff",
        })
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft or file not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.put("/authoring/drafts/{draft_id}/file")
async def put_authoring_file(
    draft_id: str, request: PutAuthoringFileRequest,
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Add or replace a supporting file using the current revision (Instructor only)."""
    try:
        repository = authoring_repository()
        return authoring_draft_response(repository, repository.put_file(
            draft_id, request.revision, request.path, request.file,
        ))
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.delete("/authoring/drafts/{draft_id}/file")
async def delete_authoring_file(
    draft_id: str, path: str, revision: str = Query(pattern=r"^(0|[1-9][0-9]*)$"),
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """Delete one supporting file using the current revision (Instructor only)."""
    try:
        repository = authoring_repository()
        return authoring_draft_response(repository, repository.delete_file(draft_id, int(revision), path))
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft or file not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.post("/authoring/drafts/{draft_id}/preview")
async def preview_authoring_draft(
    draft_id: str,
    request: AuthoringRevisionRequest,
    user: dict = Depends(require_instructor_user),
) -> Any:
    """Start a fresh, isolated Preview Lab from a saved network definition."""
    repository = authoring_repository()
    try:
        draft = repository.read(draft_id)
        if draft["revision"] != request.revision:
            raise DraftConflictError("Save or reopen this changed draft before previewing it")
        repository.validate_for_build(draft)
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)

    current_lab = registry.lab_service.lab_of(user["username"])
    # Single-Lab Mode replaces only this draft's own Preview: a rebuild.
    if (settings.lab_mode == "single" and current_lab is not None
            and current_lab.example != f"preview-{draft_id}"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Stop the shared Lab '{current_lab.example}' ({current_lab.key}) before "
                "starting a Preview Lab. Single-Lab mode cannot replace a shared Lab."
            ),
        )
    if current_lab is not None:
        if request.replace_key is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preview_replacement_required",
                    "key": current_lab.key,
                    "message": (
                        f"Stop Lab '{current_lab.example}' ({current_lab.key}) and replace it "
                        "with a fresh Preview Lab? Changes not yet kept will be lost."
                    ),
                },
            )
        if request.replace_key != current_lab.key:
            raise HTTPException(
                status_code=409,
                detail="Your current Lab changed; reopen Preview before replacing it.",
            )
        await registry.stop_lab(current_lab.key)
        await registry.broadcast_status_update()
    elif request.replace_key is not None:
        raise HTTPException(
            status_code=409,
            detail="Your current Lab has already stopped; reopen Preview to start it.",
        )

    preview_root = os.path.join(repository.root, "previews")
    os.makedirs(preview_root, exist_ok=True)
    preview_config_copy = os.path.join(
        preview_root, "config", f"{uuid.uuid4().hex}"
    )
    python = draft.get("kind") == "python"
    packaged = python or bool(draft.get("lesson_files"))
    # Supporting files stay beside either source mode throughout its Preview.
    temporary = (os.path.join(preview_config_copy, ".package") if packaged
                 else tempfile.mkdtemp(prefix="preview-", dir=preview_root))
    builder_path = os.path.join(temporary, "lab.py")
    try:
        repository.write_builder(draft, builder_path)
        preview_id = f"preview-{draft_id}"
        result = await registry.start_lab_from_builder(
            preview_id,
            builder_path,
            user["username"],
            preview=True,
            config_copy=preview_config_copy,
            cwd=temporary if packaged else None,
        )
    finally:
        if not packaged:
            shutil.rmtree(temporary, ignore_errors=True)
    if packaged and "error" in result:
        shutil.rmtree(preview_config_copy, ignore_errors=True)
    if "error" in result:
        status_code = 409 if result.get("running") or result.get("cap") else 500
        raise HTTPException(status_code=status_code,
                            detail=_at_code_section_line(draft, result["error"]))

    latest = repository.read(draft_id)
    if latest["revision"] != request.revision:
        await registry.stop_lab(result["key"])
        raise HTTPException(
            status_code=409,
            detail="The Draft changed while Preview was starting. Reopen it and preview again.",
        )
    try:
        lab = registry.lab_service.get(result["key"])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            registry.lab_service.executor,
            snapshot_preview,
            repository,
            draft_id,
            result["key"],
            lab.handle if lab is not None else None,
        )
        repository.record_preview(latest, result["key"], result.get("nodes"))
    except (OSError, ValueError, LabUnavailableError) as exc:
        await registry.stop_lab(result["key"])
        raise HTTPException(status_code=500, detail=f"Could not save Preview startup evidence: {exc}") from exc
    await registry.broadcast_status_update()
    await registry.broadcast_topology_update(result["key"])
    return {**result, "preview_valid": True}


def _at_code_section_line(draft: Dict[str, Any], error: str) -> str:
    """Point a failure inside a Canvas draft's Code section at its own line.

    The script's run names the generated script's line; the Instructor wrote
    the Code section, so its line is the one they can find and fix.
    """
    if not has_code_section(draft):
        return error
    first = code_section_first_line(draft)
    lines = len(draft["code_section"].splitlines())

    def replace(match: re.Match) -> str:
        line = int(match.group(1) or match.group(2)) - first + 1
        if not 1 <= line <= lines:
            return match.group(0)
        return f"(Code section line {line}{')' if match.group(2) else ''}"

    # A failure while running names "(line N of lab.py: …)"; a syntax error
    # names "(lab.py, line N)".
    return re.sub(r"\(line (\d+) of lab\.py|\(lab\.py, line (\d+)\)", replace, error)


class KeepSelection(BaseModel):
    """The sections chosen from one Router's reviewed diff."""

    node_id: str
    digest: str
    sections: List[StrictInt]


class FeatureKeepSelection(BaseModel):
    """One supported runtime setting selected from its reviewed change."""

    id: str
    digest: str


class KeepRequest(BaseModel):
    """Selected reviewed changes to write into the draft."""

    revision: int = Field(ge=0)
    selections: List[KeepSelection]
    features: List[FeatureKeepSelection] = Field(default_factory=list)


@router.get("/authoring/drafts/{draft_id}/keep")
async def review_authoring_keep(
    draft_id: str,
    user: dict = Depends(require_instructor_user),
) -> dict:
    """Diff supported runtime state against the Preview snapshot."""
    repository = authoring_repository()
    lab = registry.lab_service.lab_of(user["username"])
    preview_key = lab.key if lab is not None and lab.example == f"preview-{draft_id}" else None
    handle = lab.handle if preview_key is not None else None
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            registry.lab_service.executor,
            review_keep,
            repository,
            draft_id,
            preview_key,
            handle,
        )
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except (DraftConflictError, LabUnavailableError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/authoring/drafts/{draft_id}/keep")
async def apply_authoring_keep(
    draft_id: str,
    request: KeepRequest,
    user: dict = Depends(require_instructor_user),
) -> Any:
    """Write only selected, reviewed runtime changes into the draft."""
    repository = authoring_repository()
    lab = registry.lab_service.lab_of(user["username"])
    preview_key = lab.key if lab is not None and lab.example == f"preview-{draft_id}" else None
    handle = lab.handle if preview_key is not None else None
    loop = asyncio.get_running_loop()
    try:
        saved = await loop.run_in_executor(
            registry.lab_service.executor,
            apply_keep,
            repository,
            draft_id,
            request.revision,
            preview_key,
            handle,
            [selection.model_dump() for selection in request.selections],
            [selection.model_dump() for selection in request.features],
        )
    except (DraftConflictError, LabUnavailableError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)
    return authoring_draft_response(repository, saved)


@router.get("/authoring/drafts/{draft_id}/preview-status")
async def authoring_preview_status(
    draft_id: str,
    _user: dict = Depends(require_instructor_user),
) -> dict:
    """Report whether successful startup evidence matches the saved Network."""
    repository = authoring_repository()
    try:
        return repository.preview_status(repository.read(draft_id))
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/authoring/drafts/{draft_id}/python")
async def authoring_draft_python(
    draft_id: str,
    _user: dict = Depends(require_instructor_user),
) -> Any:
    """The Python a Preview of this saved draft runs (Instructor only).

    A Python draft's script is its own. A Canvas draft's is generated,
    with the text ``before`` and ``after`` the place its Code section goes.
    """
    repository = authoring_repository()
    try:
        draft = repository.read(draft_id)
        repository.validate_for_build(draft)
        filename = f"{script_slug(draft['catalog']['name'])}.py"
        if draft.get("kind") == "python":
            return {"filename": filename, "source": draft["python"]}
        before, after = generated_python_parts(draft)
        return {"filename": filename, "source": generate_python(draft),
                "before": before, "after": after}
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)


@router.get("/authoring/drafts/{draft_id}/drawing")
async def authoring_draft_drawing(
    draft_id: str,
    user: dict = Depends(require_instructor_user),
) -> dict:
    """Draw what only a saved draft's script knows (Instructor only).

    A Python draft's Nodes and Links, or those a Canvas draft's Code section
    adds, come from a record-only run of the script that builds no network.
    A script that cannot be recorded is drawn from this Instructor's running
    Preview of the draft, if there is one, with a note saying why.
    """
    repository = authoring_repository()
    try:
        draft = repository.read(draft_id)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    python = draft.get("kind") == "python"
    if not python and not has_code_section(draft):
        return {"nodes": [], "links": [], "source": "script", "note": None}
    try:
        with tempfile.TemporaryDirectory(prefix="drawing-") as package:
            script = os.path.join(package, "lab.py")
            repository.write_builder(draft, script)
            recorded = await record_script(package, script)
    except (RecordError, ValueError) as exc:
        reason = f"Could not draw the script: {_at_code_section_line(draft, str(exc))}."
        lab = registry.lab_service.lab_of(user["username"])
        topology = (await registry.get_topology(lab.key)
                    if lab is not None and lab.example == f"preview-{draft_id}" else None)
        if topology is None or "error" in topology:
            return {"nodes": [], "links": [], "source": "none",
                    "note": f"{reason} Start a Preview to draw it."}
        return {**running_drawing(topology, draft), "source": "preview",
                "note": f"{reason} Showing the running Preview's Topology."}
    if not python:
        recorded = code_section_drawing(recorded, code_section_first_line(draft))
    return {**recorded, "source": "script", "note": None}


@router.get("/authoring/drafts/{draft_id}/python.zip")
async def authoring_draft_python_package(
    draft_id: str,
    _user: dict = Depends(require_instructor_user),
) -> Response:
    """Download the generated script with its Seeds, Guide and Layout (Instructor only)."""
    repository = authoring_repository()
    try:
        draft = repository.read(draft_id)
        archive = package_archive(repository, draft)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)
    filename = f"{script_slug(draft['catalog']['name'])}.zip"
    return Response(content=archive, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/authoring/drafts/{draft_id}/publish")
async def publish_authoring_draft(
    draft_id: str,
    request: AuthoringRevisionRequest,
    user: dict = Depends(require_instructor_user),
) -> Any:
    """Publish after startup succeeds, starting a check when evidence is stale."""
    repository = authoring_repository()
    try:
        draft = repository.read(draft_id)
        if draft["revision"] != request.revision:
            raise DraftConflictError("Save or reopen this changed draft before publishing it")
        repository.validate_for_publish(draft)
        if not repository.preview_status(draft)["current"]:
            await preview_authoring_draft(draft_id, request, user)
        saved, entry = repository.publish_new(draft_id, request.revision, request.mode)
    except DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lab Example Draft not found") from exc
    except ValueError as exc:
        return _authoring_validation_response(exc)
    structure = repository.published_structure(entry["id"])
    affected = [group for group in registry.config_copies.groups_of(entry["id"])
                if not registry.config_copies.compatible(group, entry["id"], structure)]
    return {"draft": authoring_draft_response(repository, saved), "example": entry,
            "incompatible_groups": affected}


def catalog_entry(example_id: str) -> Optional[Dict[str, Any]]:
    """The Catalog entry of a shipped or authored Lab Example, if there is one."""
    return next((item for item in load_labs_manifest() if item["id"] == example_id), None)


def _authored_example(example_id: str) -> Dict[str, Any]:
    """The Catalog entry of a Lab Example an Instructor authored, or refuse it."""
    example = catalog_entry(example_id)
    if example is None:
        raise HTTPException(status_code=404, detail=f"Unknown Lab Example: {example_id}")
    if not example.get("authored"):
        raise HTTPException(status_code=422,
                            detail="A shipped Lab Example cannot be removed from the Catalog")
    return example


@router.get("/authoring/examples/{example_id}/removal")
async def authoring_example_removal(
    example_id: str,
    _user: dict = Depends(require_instructor_user),
) -> dict:
    """Name the Groups whose Config Copies removing this Lab Example discards (Instructor only)."""
    example = _authored_example(example_id)
    return {"example": example_id, "name": example["name"],
            "config_copies": registry.config_copies.groups_of(example_id)}


@router.delete("/authoring/examples/{example_id}")
async def remove_authoring_example(
    example_id: str,
    _user: dict = Depends(require_instructor_user),
) -> dict:
    """Remove an authored Lab Example from the Catalog: Publish's undoing (Instructor only).

    Every version goes, with every Group's Config Copy of it. Refused,
    naming the Group, while any Lab of it runs.
    """
    example = _authored_example(example_id)
    repository = authoring_repository()
    try:
        discarded = await registry.remove_example(
            example_id, lambda: repository.remove_published(example_id))
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Lab Example: {example_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=f"{example['name']} cannot leave the Catalog: {exc}") from exc
    return {"example": example_id, "discarded": discarded}


def require_lab(key: str, user: dict = Depends(get_current_user_with_role)) -> str:
    """Resolve the Lab key in a ``/labs/{key}/…`` path to a Lab the caller may reach.

    The URL is the binding: a key that names no Lab the caller may see is
    refused — another Group's Lab exactly as one that does not exist — and
    a key whose Lab ended on its own is refused with the reason its process
    gave, so a stale page learns what happened to its Lab rather than
    seeing another one.  With no Lab visible and no record of this key,
    the request goes through to the registry, which answers "No lab
    running" as it always has.

    Args:
        key: The Lab key from the path.
        user: The caller, supplied by the auth check.

    Returns:
        The key, once accepted.

    Raises:
        HTTPException: 404 when the key names no Lab the caller may reach.
    """
    refusal = registry.refuse_key(key, user["username"], user["role"])
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=refusal)
    return key


# Everything that reads or writes one Lab lives under its key.
lab_router = APIRouter(prefix="/labs/{key}", dependencies=[Depends(require_lab)])


class RPKIROAFields(BaseModel):
    """One ROA a Learner names: its prefix, maxLength and origin.

    ``max_length`` defaults to the prefix's own length, as ``krillc`` and the
    Lab Prompt default it.
    """

    prefix: str = Field(min_length=1)
    max_length: Optional[int] = None
    origin: Union[int, str]


class RPKIROAUpsertRequest(RPKIROAFields):
    """A ROA a Learner is signing, or an edit of one: ``old`` is the ROA it replaces."""

    old: Optional[RPKIROAFields] = None


class RPKIASPAFields(BaseModel):
    """One ASPA a Learner names: its customer and the providers it lists."""

    customer: Union[int, str]
    providers: List[Union[int, str]] = Field(default_factory=list)


class RPKIASPARemoveRequest(BaseModel):
    """The customer whose ASPA a Learner is withdrawing."""

    customer: Union[int, str]


class DNSRecordUpsertRequest(BaseModel):
    """One Resource Record a Learner is adding or updating from the Web UI."""

    owner: str = Field(min_length=1)
    type: str = Field(min_length=1)
    data: str = Field(min_length=1)
    ttl: Optional[int] = Field(default=None, gt=0)
    old_owner: Optional[str] = None
    old_type: Optional[str] = None
    old_data: Optional[str] = None


class DNSRecordDeleteRequest(BaseModel):
    """One Resource Record a Learner is removing from the Web UI.

    ``type`` and ``data`` narrow the deletion: omitting ``data`` removes all
    data of the given type at the owner; omitting both removes the owner.
    """

    owner: str = Field(min_length=1)
    type: Optional[str] = Field(default=None, min_length=1)
    data: Optional[str] = Field(default=None, min_length=1)


class DNSSECRolloverRequest(BaseModel):
    """Parameters for on-demand DNSSEC key rollover."""

    key_type: str = Field(default="zsk", pattern="^(zsk|ksk)$")


class ResolverFlushRequest(BaseModel):
    """The cache entries a Learner is evicting from a running Resolver.

    Omitting ``target`` clears the whole cache; ``scope`` chooses between
    evicting exactly that name and evicting it with everything beneath it.
    """

    target: Optional[str] = None
    scope: str = "name"


class AccessRuleRequest(BaseModel):
    """One Access Control rule a Learner is authoring.

    ``action`` is left an open string rather than an enum here: the vocabulary
    belongs to :class:`mniplab.resolver.models.AccessRule`, which rejects an
    unknown one by naming the three that are valid. Restating the list here
    would trade that explanation for a schema error, and put the vocabulary in
    two places.
    """

    subnet: str = Field(min_length=1)
    action: str = Field(default="allow", min_length=1)


class ResolverAccessRequest(BaseModel):
    """The Access Control policy a Learner is putting in force.

    The whole policy is submitted at once rather than one rule at a time, so
    adding, editing, and deleting a rule all reach the daemon as a single hot
    reload. Order is preserved for display only: Unbound itself matches by
    longest prefix, so a narrower Subnet wins wherever it sits in the list.
    """

    rules: list[AccessRuleRequest] = Field(default_factory=list)


class SwitchPortVlanRequest(BaseModel):
    """Request model for configuring a switch port's 802.1Q VLAN membership."""

    mode: str = Field(..., description="VLAN mode: 'access', 'trunk', or 'none'")
    vlan: Optional[Union[int, str]] = Field(None, description="VLAN ID for access mode (1-4094)")
    tag: Optional[Union[int, str]] = Field(None, description="Alias for vlan ID")
    trunks: Optional[Union[List[Union[int, str]], str]] = Field(
        None, description="VLAN ID(s) for trunk mode (list of ints/strings or comma-separated string)"
    )
    native: Optional[Union[int, str]] = Field(
        None, description="Optional native untagged VLAN ID (1-4094) for trunk mode"
    )


@router.post("/login")
async def login(
    response: Response,
    credentials: HTTPBasicCredentials = Depends(security),
    session: Optional[str] = Cookie(None),
) -> dict:
    """Login, sweeping idle Sessions, and replacing the caller's own.

    Args:
        response: The response whose session cookie is set.
        credentials: The HTTP Basic credentials presented.
        session: The session cookie the browser already held, if any.

    Returns:
        The authenticated username and its role.

    Raises:
        HTTPException: 429 from too many login attempts, 401 for bad
            credentials, or 403 if the account is at the Session Cap and no
            Session can be evicted to make room.
    """
    username = verify_credentials(credentials)

    # A login is the one moment worth scanning the whole store: it is when a
    # stale Session starts to matter, and it costs a pass over a dict of tens.
    # Swept after the credentials are checked, so an unauthenticated caller
    # cannot drive the scan.
    await expire_idle_sessions()

    # Evaluated before the browser's own outgoing Session is touched, with
    # that Session excluded from the count: the arithmetic must already treat
    # a replacing login as freeing its own slot, but the Session itself must
    # survive until it is known the login will not be refused.  Destroying it
    # first would mean a refused Learner lost the Session their browser
    # already held, on top of the refusal.  Retrying after this 403 costs
    # nothing against the login limit, which counts wrong passwords only.
    if not await make_room_for_session(username, exclude=session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You are signed in on too many devices. Sign out on another "
                "device, or wait for an idle Session to expire, and try again."
            ),
        )

    # One browser holds one Session per account, so the cookie this request
    # carried names a record about to become unreachable.  Retired after the
    # sweep and the cap rather than before them: a prior Session already past
    # the idle timeout is then logged as expired, which is what it is, and
    # this step never needs its own copy of the idle boundary.
    await destroy_previous_session(session, username)

    role = settings.get_user_registry()[username]["role"]
    session_id = create_session(username, role)
    
    # Set secure HTTP-only cookie
    response.set_cookie(
        key="session",
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=settings.use_secure_cookies,
        max_age=86400  # 24 hours
    )
    
    return {"authenticated": True, "username": username, "role": role}


@router.post("/logout")
async def logout(
    response: Response,
    session: str = Cookie(None)
) -> dict:
    """Logout, destroying the Session and closing every Terminal it owns."""
    if session:
        await destroy_session(session)
    
    # Clear cookie
    response.delete_cookie(key="session")
    
    return {"logged_out": True}


@router.get("/me")
async def get_me(
    user: dict = Depends(get_current_user_with_role),
    session: Optional[str] = Cookie(None),
) -> dict:
    """Describe the caller: account, role, and the manager's mode (authenticated).

    A login is a Group, so ``group`` is the username; the browser renders
    the Group page and the Instructor's dashboard only when ``mode`` is
    ``multi``.
    """
    return {
        "username": user["username"],
        "role": user["role"],
        "group": user["username"],
        "mode": settings.lab_mode,
        "cap": registry.lab_cap,
        # Only `serve` has Server settings; the page offers them to an Instructor.
        "server_settings": registry.settings_store is not None,
        **auth.session_identity(session),
    }


@router.patch("/me")
async def rename_session(
    request: SessionNicknameRequest,
    _user: dict = Depends(get_current_user_with_role),
    session: Optional[str] = Cookie(None),
) -> dict:
    """Rename only the caller's Session without changing its connection code."""
    identity = auth.session_identity(session)
    auth.active_sessions[session or ""]["nickname"] = request.nickname
    return {**identity, "nickname": request.nickname}


@router.get("/status")
async def get_status(user: dict = Depends(get_current_user_with_role)) -> dict:
    """The status of the caller's Lab (authenticated).

    The one Lab in Single-Lab Mode; the caller's Group's own in Multi-Lab
    Mode, with why its last Lab ended when none is running.
    """
    return await registry.get_status(user["username"], user["role"])


@router.get("/admin/terminals")
async def get_terminals(user: dict = Depends(require_role("instructor"))) -> dict:
    """List every live Terminal (instructor only)."""
    terminals = await registry.get_terminals()
    return {"terminals": terminals, "total": len(terminals)}


@router.post("/admin/terminals/close")
async def close_terminal(
    request: CloseTerminalRequest,
    user: dict = Depends(require_role("instructor"))
) -> dict:
    """Force close one Terminal (instructor only)."""
    await registry.close_terminal(request.terminal_id)
    return {"closed": True, "terminal_id": request.terminal_id}


@router.get("/admin/sessions")
async def get_sessions(_user: dict = Depends(require_role("instructor"))) -> dict:
    """List the Sessions signed in right now (instructor only).

    Distinct from ``/admin/terminals``: this is the glossary's Session — one
    person's authenticated connection — and it is what an Instructor needs
    when a Learner cannot get in.

    Args:
        _user: The Instructor making the request, supplied by the role check.

    Returns:
        One entry per live Session, and how many there are.
    """
    # Swept first, the same way login does: an Instructor reads this list to
    # decide whether to intervene, and a ghost tab shown as somebody still
    # signed in is exactly the wrong answer.
    await expire_idle_sessions()
    sessions = list_sessions()
    return {"sessions": sessions, "total": len(sessions)}


@router.post("/admin/sessions/logout")
async def force_logout_session(
    request: ForceLogoutRequest,
    _user: dict = Depends(require_role("instructor")),
) -> dict:
    """End one Session on the Learner's behalf (instructor only).

    The escape hatch for a Learner who is stuck and cannot be freed by waiting
    for the idle sweep or an eviction at the Session Cap.  Destroying the
    Session closes the Terminals it holds, so the Learner's allowance goes with
    it.

    An id naming no live Session is a no-op rather than an error: by the time
    the Instructor clicks, the Session may already have expired or signed
    itself out, and the outcome they asked for — that Session gone — holds
    either way.

    Args:
        request: The Session to end.
        _user: The Instructor making the request, supplied by the role check.

    Returns:
        The Session's id, and whether a live Session was actually destroyed.
    """
    logged_out = await destroy_session(request.session_id)
    return {"logged_out": logged_out, "session_id": request.session_id}


def _activity_state(
    name: str,
    lab: Optional[Dict[str, Any]],
    last_exit: Optional[Dict[str, Any]],
    sessions: Dict[str, float],
    terminals: Dict[str, float],
    now: float,
    threshold: int,
) -> Tuple[str, Optional[str]]:
    """Derive one Group's Activity State at request time.

    Absent when the Group holds no live Session; Stopped when it holds one
    but no Lab runs, carrying the exit reason when the Lab ended on its
    own; Active or Idle — against ``threshold`` — from the newer of the
    Session's last sighting and the newest Terminal on the Group's Lab.

    Args:
        name: The Group's name, as its Session's username.
        lab: The Group's running Lab row, or None.
        last_exit: The Group's most recent Lab exit record, or None.
        sessions: Username → last-seen time of the live Sessions.
        terminals: Lab key → newest Terminal activity time.
        now: The current time, from the Session clock.
        threshold: Seconds of quiet that turn Active into Idle.

    Returns:
        The state and, when Stopped, the reason it stopped.
    """
    if name not in sessions:
        return "absent", None
    if lab is None:
        return "stopped", (last_exit or {}).get("reason") or "stopped"
    activity = max(sessions[name], terminals.get(lab["key"], 0.0))
    return ("active" if now - activity <= threshold else "idle"), None


@router.get("/admin/dashboard")
async def get_dashboard(_user: dict = Depends(require_role("instructor"))) -> dict:
    """The class at a glance (instructor only).

    Every Group with its Lab, its Assignment, the Lab Examples it holds a
    copy of Persistent Config for, its Activity State — Active, Idle,
    Stopped (with a reason) or Absent, derived from the live Sessions and
    Terminals — and, when its last Lab ended on its own, why; every Lab
    with its Group, Lab Example, uptime, Terminals and key, the key being
    the diagnostic that matches ``ovs-vsctl show`` or ``docker ps`` on the
    host to a Group.

    Returns:
        The mode, the cap, the Groups, the Labs and display names keyed by ID.
    """
    labs = [_lab_row(await registry.status_of(lab)) for lab in registry.lab_service.labs()]
    by_group = {row["group"]: row for row in labs}
    sessions: Dict[str, float] = {}
    session_counts: Dict[str, int] = {}
    learner_inputs: Dict[str, float] = {}
    for session in list_sessions():
        name = session["username"]
        session_counts[name] = session_counts.get(name, 0) + 1
        if session["role"] == "learner" and session.get("last_input_at") is not None:
            learner_inputs[name] = max(
                learner_inputs.get(name, 0.0), session["last_input_at"]
            )
        seen = session["last_seen"]
        if seen > sessions.get(session["username"], 0.0):
            sessions[session["username"]] = seen
    terminals: Dict[str, float] = {}
    for terminal in registry.terminal_service.terminals.values():
        seen = terminal.last_activity
        if seen > terminals.get(terminal.lab_key, 0.0):
            terminals[terminal.lab_key] = seen
    now = auth.session_clock()
    threshold = settings.activity_idle_threshold
    repository = authoring_repository()
    examples = load_labs_manifest()
    example_names = {item["id"]: item.get("name") or item["id"] for item in examples}
    for lab in labs:
        example = lab.get("example") or ""
        if example:
            example_names[example] = lab.get("example_name") or example_names.get(example, example)
    authored_structures = {
        item["id"]: repository.published_structure(item["id"])
        for item in examples if item.get("authored")
    }
    groups = []
    for entry in registry.groups.list_groups():
        name = entry["name"]
        group_lab = by_group.get(name)
        last_exit = None if name in by_group else registry.lab_service.last_exit_of(name)
        state, reason = _activity_state(
            name, group_lab, last_exit, sessions, terminals, now, threshold
        )
        copies = registry.config_copies.examples_of(name)
        incompatible = [example for example in copies
                        if example in authored_structures
                        and not registry.config_copies.compatible(
                            name, example, authored_structures[example])]
        groups.append(
            {
                "name": name,
                "role": entry["role"],
                "assignment": registry.groups.assignment(name),
                "config_copies": copies,
                "incompatible_config_copies": incompatible,
                "lab": group_lab,
                "last_exit": last_exit,
                "activity_state": state,
                "session_count": session_counts.get(name, 0),
                "last_learner_input": learner_inputs.get(name),
                "last_activity": max(
                    sessions.get(name, 0.0),
                    terminals.get(group_lab["key"], 0.0) if group_lab else 0.0,
                ),
                "stop_reason": reason,
            }
        )
    return {
        "mode": settings.lab_mode,
        "cap": registry.lab_cap,
        "groups": groups,
        "labs": labs,
        "example_names": example_names,
    }


@router.get("/admin/assignments")
async def get_assignments(_user: dict = Depends(require_role("instructor"))) -> dict:
    """List every Group with its Assignment (instructor only).

    Returns:
        The Groups that can log in, each with its role, and every Group's
        Assignment keyed by Group name.
    """
    return {
        "groups": registry.groups.list_groups(),
        "assignments": registry.groups.assignments(),
    }


@router.put("/admin/assignments/{group}")
async def set_assignment(
    group: str,
    request: AssignmentRequest,
    _user: dict = Depends(require_role("instructor")),
) -> dict:
    """Set one Group's Assignment (instructor only).

    Saved beside the server's settings, so it is there after a restart.
    A running Lab is never touched: a Group whose Assignment loses the Lab
    Example it is on keeps that Lab until it is stopped.

    Args:
        group: The Group's name — a login from the users setting.
        request: The Lab Examples it may start.

    Returns:
        The Group and its Assignment as saved.

    Raises:
        HTTPException: 404 when no such Group can log in, 400 when an id
            names no Lab Example in the catalog.
    """
    try:
        example_ids = registry.groups.assign(group, request.example_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No Group named '{group}' can log in")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"group": group, "example_ids": example_ids}


@router.post("/admin/config/reset")
async def reset_config(
    request: ConfigResetRequest,
    _user: dict = Depends(require_role("instructor")),
) -> dict:
    """Reset a Group's copy of a Lab Example's Persistent Config to the seed (instructor only).

    Names one Group, or every Group holding a copy when ``group`` is
    omitted.  The copy is discarded, so that Group's next start of the Lab
    Example seeds afresh.  Refused, with nothing reset, while a named
    Group's Lab on that Lab Example is alive.

    Args:
        request: The Lab Example, and the Group or none for every Group.

    Returns:
        The Lab Example and the Groups whose copy was discarded.

    Raises:
        HTTPException: 400 for a Lab Example not in the catalog, 404 for a
            Group that cannot log in, 409 naming a Lab that is alive.
    """
    example_id = request.example_id.strip()
    if example_id not in catalog_ids():
        raise HTTPException(status_code=400, detail=f"Unknown Lab Example: {example_id}")
    if request.group is not None:
        if registry.groups.role_of(request.group) is None:
            raise HTTPException(status_code=404, detail=f"No Group named '{request.group}' can log in")
        groups = [request.group]
    else:
        groups = registry.config_copies.groups_of(example_id)
    try:
        reset = await registry.reset_config(example_id, groups)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"example": example_id, "reset": reset}


_Store = TypeVar("_Store", SettingsStore, LoginStore)


def _serve_only(store: Optional[_Store]) -> _Store:
    """A store of Server settings, which only a server started with ``serve`` has.

    Args:
        store: The Settings Store or the login store, None outside ``serve``.

    Raises:
        HTTPException: 404 under a Lab script's ``--enable-web``.
    """
    if store is None:
        raise HTTPException(status_code=404, detail="Server settings exist only under serve")
    return store


@router.get("/admin/server-settings")
async def get_server_settings(_user: dict = Depends(require_role("instructor"))) -> dict:
    """The Limits an Instructor may change, with their values (instructor, serve only).

    Returns:
        Each Limit offered in the Lab mode with its value and bounds.
    """
    return _serve_only(registry.settings_store).describe()


@router.put("/admin/server-settings")
async def save_server_settings(
    request: ServerSettingsRequest,
    user: dict = Depends(require_role("instructor")),
) -> Any:
    """Save some Limits, all or none, applying them at once (instructor, serve only).

    Args:
        request: The Limits to change, by name.

    Returns:
        The Limits as they now stand, or a 422 naming the field refused,
        with nothing changed.
    """
    store = _serve_only(registry.settings_store)
    try:
        store.save(request.limits, user["username"])
    except LimitRefused as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc), "field": exc.field})
    return store.describe()


@router.get("/admin/logins")
async def list_logins(_user: dict = Depends(require_role("instructor"))) -> dict:
    """Every login the server accepts, without passwords (instructor, serve only).

    Returns:
        Each login's name, its role, and whether it was added in Server
        settings rather than set in ``.env``.
    """
    _serve_only(settings.login_store)
    return {
        "logins": [
            {"name": name, "role": login["role"], "added": bool(login.get("added"))}
            for name, login in settings.get_user_registry().items()
        ]
    }


@router.post("/admin/logins")
async def add_login(
    request: AddLoginRequest,
    user: dict = Depends(require_role("instructor")),
) -> Any:
    """Add a Learner login with a generated password (instructor, serve only).

    Args:
        request: The new login's name.

    Returns:
        The name and its password, a 422 for a name outside the rule, or a
        409 for a name already a login.
    """
    store = _serve_only(settings.login_store)
    try:
        password = store.add(request.name, settings.get_user_registry(), user["username"])
    except LoginTaken as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except LoginRefused as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return {"name": request.name, "password": password}


@router.get("/admin/logins/{name}/password")
async def show_login_password(
    name: str,
    user: dict = Depends(require_role("instructor")),
) -> dict:
    """A page-added login's password, read back (instructor, serve only).

    Args:
        name: The login.

    Returns:
        Its name and password.

    Raises:
        HTTPException: 404 for an ``.env`` account or no such login.
    """
    password = _added_login(name).show(name, user["username"])
    return {"name": name, "password": password}


def _added_login(name: str) -> LoginStore:
    """The login store, after checking ``name`` was added in Server settings.

    An ``.env`` account wins a hand-edited collision, so its name is not one.

    Raises:
        HTTPException: 404 for an ``.env`` account or no such login.
    """
    store = _serve_only(settings.login_store)
    if not settings.get_user_registry().get(name, {}).get("added"):
        raise HTTPException(status_code=404, detail=f"{name} was not added in Server settings")
    return store


@router.post("/admin/logins/{name}/reset")
async def reset_login_password(
    name: str,
    user: dict = Depends(require_role("instructor")),
) -> dict:
    """Replace a page-added login's password and sign out everyone using the old one.

    The Group's Lab, Assignment and Config Copy are untouched.

    Args:
        name: The login.

    Returns:
        Its name and new password.

    Raises:
        HTTPException: 404 for an ``.env`` account or no such login.
    """
    password = _added_login(name).reset(name, user["username"])
    await destroy_user_sessions(name)
    return {"name": name, "password": password}


@router.delete("/admin/logins/{name}")
async def remove_login(
    name: str,
    user: dict = Depends(require_role("instructor")),
) -> dict:
    """Remove a page-added login and end its Sessions and Terminals.

    In Multi-Lab Mode the Group's running Lab is stopped and its
    Assignment dropped; its Config Copy stays, so a login added again
    under the same name resumes the Learners' work.

    Args:
        name: The login.

    Returns:
        Its name and that it was removed.

    Raises:
        HTTPException: 404 for an ``.env`` account or no such login.
    """
    _added_login(name).remove(name, user["username"])
    await destroy_user_sessions(name)
    if settings.lab_mode == "multi":
        await registry.remove_group(name)
    return {"name": name, "removed": True}


@router.get("/admin/metrics")
async def get_metrics(user: dict = Depends(require_role("instructor"))) -> dict:
    """Get active lab metrics (instructor only)."""
    lab_status = await registry.get_status(user["username"], user["role"])
    terminals = await registry.get_terminals()
    connected_users = len({t.get("username", t["client"]) for t in terminals})
    return {
        "active_terminals": len(terminals),
        "connected_users": connected_users,
        "lab_uptime": lab_status.get("uptime", 0),
    }


@router.post('/save_layout')
async def save_layout(
    request: SaveLayoutRequest,
    _user: dict = Depends(require_role("instructor"))
) -> dict:
    """Save topology layout to examples/layouts/<lab>.json (instructor only).

    The browser POSTs the nodes' current raw positions; the server normalizes
    them into the canonical virtual-canvas schema before writing. The `example`
    value is sanitized to avoid path traversal. If writing to the workspace
    `examples/` directory fails (permissions), this will fall back to `/tmp` and
    return the actual saved path.
    """
    if request.example and request.example.startswith(("authored-", "preview-")):
        raise HTTPException(
            status_code=409,
            detail="Edit this Authored Layout in its Lab Example Draft before publishing an update",
        )
    positions = request.positions
    if not isinstance(positions, dict):
        raise HTTPException(status_code=400, detail='positions must be an object')
    if len(positions) > 1000:
        raise HTTPException(status_code=400, detail='positions too large')
    for nid in positions:
        if not isinstance(nid, str):
            raise HTTPException(status_code=400, detail='invalid node id')

    # normalize is the single coordinate validator: it refuses any node without
    # numeric x/y, which we surface as a 400 rather than a 500.
    try:
        canonical = normalize(positions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return LayoutRepository.save(request.example, canonical)


def _authored_asset_for_viewer(example: str, filename: str, key: Optional[str], user: dict) -> str:
    """Resolve the Catalog asset, or the immutable version a visible Lab started."""
    if key is None:
        return authoring_repository().published_file(example, filename)
    lab = registry.lab_service.get(key)
    if (lab is None or not lab.visible_to(user["username"], user["role"])
            or lab.example != example or lab.builder_file is None):
        raise HTTPException(status_code=404, detail="Running Lab not found")
    path = os.path.realpath(os.path.join(os.path.dirname(lab.builder_file), filename))
    root = os.path.realpath(os.path.join(authored_labs_root(), "published"))
    if not path.startswith(root + os.sep):
        raise HTTPException(status_code=404, detail="Running Lab asset not found")
    return path


@router.get('/load_layout')
async def load_layout(
    example: Optional[str] = None,
    key: Optional[str] = None,
    user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Load the canonical Authored Layout for `example`.

    Returns an object with a `layout` key holding the canonical
    `{version, nodes:{id:{x,y}}}` document, or `null` when the lab has no
    Authored Layout. A file that exists but is malformed is refused loudly.
    """
    layout: Optional[Dict[str, Any]]
    if example and example.startswith("preview-") and user["role"] != "instructor":
        raise HTTPException(status_code=404, detail="Authored Layout not found")
    try:
        if example and example.startswith("preview-"):
            draft_id = example.removeprefix("preview-")
            repository = authoring_repository()
            layout = repository.layout_for_runtime(repository.read(draft_id))
        elif example and example.startswith("authored-"):
            path = _authored_asset_for_viewer(example, "layout.json", key, user)
            with open(path, "r", encoding="utf-8") as source:
                layout = json.load(source)
            LayoutRepository.validate_canonical(layout)
        else:
            layout = LayoutRepository.load(example)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f'malformed layout: {exc}') from exc
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Authored Layout not found") from exc
    return {'layout': layout}


@router.get('/load_guide')
async def load_guide(
    example: Optional[str] = None,
    key: Optional[str] = None,
    user: dict = Depends(get_current_user_with_role)
) -> Response:
    """Load lab guide markdown from examples/guides/<example>.md.

    The example name is sanitized to prevent path traversal.
    Returns HTTP 404 when the guide file is not available.
    """
    if example and example.startswith("preview-") and user["role"] != "instructor":
        raise HTTPException(status_code=404, detail="Guide not found")
    examples_dir = resolve_examples_dir()
    try:
        if example and example.startswith("preview-"):
            draft_id = example.removeprefix("preview-")
            markdown = authoring_repository().read(draft_id)["guide"]
        elif example and example.startswith("authored-"):
            path = _authored_asset_for_viewer(example, "guide.md", key, user)
            with open(path, "r", encoding="utf-8") as source:
                markdown = source.read()
        else:
            markdown = GuideRepository.load(example, examples_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid guide path')
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='Guide not found')
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Guide not found") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail='Failed to read guide') from exc

    return Response(content=markdown, media_type='text/markdown; charset=utf-8')


@lab_router.post("/test/ping")
async def run_ping(
    key: str,
    request: PingRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Run ping test between two nodes (authenticated)."""
    return await registry.run_ping(key, request.source, request.destination, request.count,
                                   ipv6=request.ipv6)


@lab_router.post("/test/traceroute")
async def run_traceroute(
    key: str,
    request: TracerouteRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Run traceroute test between two nodes (authenticated)."""
    return await registry.run_traceroute(key, request.source, request.destination, request.ipv6,
                                         resolve=request.resolve)


@lab_router.post("/test/iperf")
async def run_iperf(
    key: str,
    request: IperfRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Run iperf bandwidth test (authenticated)."""
    return await registry.run_iperf(key, request.server, request.client)


@lab_router.post("/test/iperf3")
async def run_iperf3(
    key: str,
    request: Iperf3Request,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Run iperf3 bandwidth test with configurable options (authenticated)."""
    return await registry.run_iperf3(
        key,
        request.server,
        request.client,
        request.seconds,
        request.protocol,
        request.json_output
    )


@lab_router.post("/link/disconnect")
async def disconnect_link(
    key: str,
    request: LinkStatusRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Disconnect a link (authenticated)."""
    return await registry.disconnect_link(key, request.node1, request.node2)


@lab_router.post("/link/reconnect")
async def reconnect_link(
    key: str,
    request: LinkStatusRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Reconnect a link (authenticated)."""
    return await registry.reconnect_link(key, request.node1, request.node2)


@lab_router.post("/link/configure")
async def link_configure(
    key: str,
    request: LinkConfigureRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Configure link parameters (authenticated)."""
    return await registry.link_configure(
        key,
        request.node1, request.node2,
        request.delay, request.jitter, request.loss,
        request.bandwidth, request.scheduler
    )


@router.get('/examples')
async def list_examples(user: dict = Depends(get_current_user_with_role)) -> dict:
    """List the Lab Examples the caller may start (authenticated).

    The whole catalog, examples/labs.json, in Single-Lab Mode; in Multi-Lab
    Mode a Group sees its Assignment, and the Instructor everything.  There
    each row also says whether the Lab Example has Persistent Config
    (``persistent_config``) and whether the caller's Group's next start of
    it resumes their copy rather than seeding (``resumes``).
    """
    examples = load_labs_manifest()
    if settings.lab_mode == 'multi':
        group = user["username"]
        assigned = set(registry.groups.assignment(group))
        examples = [
            {
                **example,
                'persistent_config': bool(example.get('persistent_config', False)),
                'resumes': registry.config_copies.exists(group, example['id']),
            }
            for example in examples if example.get('id') in assigned
        ]
    # Whether the Lab Example ships a guide, read at request time like
    # ``resumes`` above: only half the catalogue does, and a caller offering
    # Open Guide on a row without one promises material that is not there.
    examples_dir = resolve_examples_dir()
    repository = authoring_repository()
    with_guide = []
    for example in examples:
        has_guide = GuideRepository.available(example.get('id'), examples_dir)
        incompatible = False
        if (settings.lab_mode == "multi" and example.get("authored")
                and registry.config_copies.exists(user["username"], example["id"])):
            incompatible = not registry.config_copies.compatible(
                user["username"], example["id"], repository.published_structure(example["id"])
            )
        with_guide.append({**example, "area": example.get("area") or "Other",
                           "has_guide": has_guide, "incompatible": incompatible})
    examples = with_guide
    return {'examples': examples, 'areas': list(AREAS)}


@router.get('/labs')
async def list_labs(user: dict = Depends(get_current_user_with_role)) -> dict:
    """List the Labs the caller may see (authenticated).

    In Single-Lab Mode that is the one Lab everyone shares, or nothing; in
    Multi-Lab Mode a Group sees its own Lab and the Instructor every Lab.
    """
    labs = registry.lab_service.visible(user["username"], user["role"])
    return {'labs': [_lab_row(await registry.status_of(lab)) for lab in labs]}


def _lab_row(lab_status: dict) -> dict:
    """Shape a running Lab's status as one row of the Lab list."""
    return {
        key: lab_status.get(key)
        for key in (
            "key", "group", "example", "example_name", "uptime", "nodes", "active_terminals",
            "link_condition_drift", "has_guide",
        )
    }


def _may_manage_labs(user: dict) -> bool:
    """Whether a login may start and stop Labs.

    The Instructor always may; in Multi-Lab Mode every Group manages its
    own Lab.
    """
    return user["role"] == "instructor" or settings.lab_mode == 'multi'


@router.post('/labs')
async def start_lab(
    request: StartLabRequest,
    user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Start a Lab Example by its catalog id, in a process of its own.

    The Instructor may start any Lab Example for their own Group, or — in
    Multi-Lab Mode, naming ``group`` — one from another Group's Assignment
    for that Group; a Group may start what its Assignment holds, for itself.  A Group holds one Lab at a time and
    the cap refuses everyone alike; both answer 409 naming the Lab or the
    cap.  The answer carries the Lab's key, under which every other route
    reaches it.
    """
    if not _may_manage_labs(user):
        raise HTTPException(status_code=403, detail="Instructor role required")
    example_id = (request.example_id or '').strip()
    group = user["username"]
    if request.group is not None and request.group != group:
        if user["role"] != "instructor" or settings.lab_mode != 'multi':
            raise HTTPException(status_code=403, detail="Only the Instructor starts a Lab for another Group")
        if registry.groups.role_of(request.group) is None:
            raise HTTPException(status_code=404, detail=f"No Group named '{request.group}' can log in")
        group = request.group

    # Validate against allowlist — no arbitrary file execution
    examples = load_labs_manifest()
    known = {example['id']: example for example in examples}
    if example_id not in known:
        raise HTTPException(status_code=400, detail=f"Unknown Lab Example: {example_id}")
    if settings.lab_mode == 'multi' and registry.groups.role_of(group) != "instructor" \
            and not registry.groups.may_start(group, example_id):
        raise HTTPException(
            status_code=403,
            detail=f"Lab Example '{example_id}' is not in Group '{group}'s Assignment",
        )

    example_meta = known[example_id]
    structure = None
    cwd = None
    if example_meta.get("authored"):
        try:
            repository = authoring_repository()
            example_path = os.path.realpath(repository.published_file(example_id, "lab.py"))
            with open(os.path.join(os.path.dirname(example_path), "definition.json"),
                      encoding="utf-8") as source:
                definition = json.load(source)
            structure = repository.persistent_structure(definition)
            if definition.get("kind") == "python" or definition.get("lesson_files"):
                # Relative inputs resolve from the immutable version folder.
                cwd = os.path.dirname(example_path)
        except DraftNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Published Lab Example not found") from exc
        authored_root = os.path.realpath(os.path.join(authored_labs_root(), "published"))
        if not example_path.startswith(authored_root + os.sep):
            raise HTTPException(status_code=400, detail="Invalid authored Lab path")
    else:
        example_filename = example_meta.get('file', f'{example_id}.py')
        examples_dir = resolve_examples_dir()
        example_path = os.path.realpath(os.path.join(examples_dir, example_filename))
        if not example_path.startswith(os.path.realpath(examples_dir) + os.sep):
            raise HTTPException(status_code=400, detail="Invalid lab path")
    if not os.path.isfile(example_path):
        label = example_id if example_meta.get("authored") else example_filename
        raise HTTPException(status_code=404, detail=f"Lab Example file not found: {label}")

    result = await registry.start_lab_from_builder(
        example_id, example_path, group, clean=request.clean, structure=structure, cwd=cwd
    )

    if 'error' in result:
        code = 409 if result.get('running') or result.get('incompatible') or 'cap' in result else 500
        raise HTTPException(status_code=code, detail=result['error'])

    # Broadcast updated topology and status to WebSocket subscribers
    await registry.broadcast_status_update()
    await registry.broadcast_topology_update(result["key"])

    return result


@lab_router.get('')
async def get_lab(key: str) -> dict:
    """Describe one running Lab (authenticated)."""
    lab = registry.lab_service.get(key)
    if lab is None:
        raise HTTPException(status_code=404, detail="No lab running")
    return _lab_row(await registry.status_of(lab))


@lab_router.post('/stop')
async def stop_lab(key: str, user: dict = Depends(get_current_user_with_role)) -> dict:
    """Stop this Lab and close every Terminal on it.

    The Instructor may stop any Lab; in Multi-Lab Mode a Group may stop
    its own.
    """
    if not _may_manage_labs(user):
        raise HTTPException(status_code=403, detail="Instructor role required")
    result = await registry.stop_lab(key)
    if 'error' in result:
        raise HTTPException(status_code=409, detail=result['error'])
    await registry.broadcast_status_update()
    return result


@lab_router.get("/topology")
async def get_topology(key: str, _user: dict = Depends(get_current_user_with_role)) -> dict:
    """Get topology data for visualization (authenticated)."""
    topology = await registry.get_topology(key)
    if "error" in topology:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=topology["error"]
        )
    return topology


@lab_router.get("/routing/routes/{node}")
async def get_node_routes(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get node routing table (authenticated)."""
    return await registry.get_node_routes(key, node)


@lab_router.get("/routing/ospf/{node}")
async def get_node_ospf(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get node OSPF info (authenticated)."""
    return await registry.get_node_ospf(key, node)


@lab_router.get("/routing/bgp/{node}")
async def get_node_bgp(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get node BGP info (authenticated)."""
    return await registry.get_node_bgp(key, node)


@lab_router.get("/dhcp/leases/{node}")
async def get_node_leases(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get the Leases a Node's DHCP Service currently holds (authenticated).

    Reported separately from configured Interface addresses: a Lease is the
    server's own record and may exist before any Interface reflects it.
    """
    return await registry.get_node_leases(key, node)


@lab_router.get("/dhcp/relay/activity/{node}")
async def get_node_relay_activity(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get the DHCP requests a Node's Relay has forwarded (authenticated)."""
    return await registry.get_node_relay_activity(key, node)


@lab_router.get("/dhcp/pools/{node}")
async def get_node_pools(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get the Pools a Node's DHCP Service currently serves (authenticated).

    A Pool is Exercise Configuration authorable by Learners.
    """
    return await registry.get_node_pools(key, node)


@lab_router.post("/dhcp/pools/{node}")
async def set_node_pool(
    key: str,
    node: str,
    request: PoolUpdateRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Update a Pool's editable fields on the running server (authenticated).

    This is Exercise Configuration: authorable by Learners, applied
    to the running Kea without a restart, and preserved across existing Leases.
    An invalid Pool is rejected before it is applied, leaving the running server
    exactly as it was.
    """
    return await registry.set_node_pool(
        key,
        node, request.cidr, request.start, request.end,
        lease_time=request.lease_time,
        preferred_lifetime=request.preferred_lifetime,
        router=request.router,
        dns_server=request.dns_server,
        reservations=[r.model_dump() for r in request.reservations],
    )


@lab_router.get("/speakers/{node}")
async def get_speaker(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Get a Speaker's Neighbors with state, and both RIBs per Neighbor (authenticated).

    Any role: reading a Speaker is a lever in a running Lab. A Node that is
    not a Speaker answers 404; a daemon that answers ``error`` or falls
    silent answers 502 naming the command.
    """
    return await registry.get_speaker(key, node)


@lab_router.post("/speakers/{node}/announce")
async def announce_speaker_route(
    key: str,
    node: str,
    request: AnnounceRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Announce a route from a Speaker (authenticated, any role).

    The body carries the same fields the CLI's ``announce`` line does; it is
    validated before anything reaches ExaBGP, and a violation answers 400
    naming the field. ``neighbor`` picks one Neighbor; without it the route
    goes to every Neighbor carrying the prefix's family. 404 for a Node
    that is not a Speaker; 502 naming the command when ExaBGP answers
    ``error`` or falls silent.
    """
    fields = request.model_dump(exclude={"neighbor"})
    return await registry.announce_route(key, node, fields, neighbor=request.neighbor)


@lab_router.post("/speakers/{node}/generate")
async def generate_speaker_routes(
    key: str,
    node: str,
    request: GenerateRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Announce a burst of generated routes from a Speaker (authenticated, any role).

    The body carries ``count`` and optionally ``neighbor`` (required when
    the Speaker has several), ``sub_prefixes``, ``prefix_seed`` and
    ``as_path_seed`` — the CLI's ``generate`` options. The answer lists
    every route announced, so a burst can be withdrawn by hand; nothing is
    tracked. 400 names the field for a bad value; 404 for a Node that is
    not a Speaker; 502 naming the command when ExaBGP rejects an announce.
    """
    return await registry.generate_routes(
        key,
        node, request.neighbor, request.count,
        sub_prefixes=request.sub_prefixes,
        prefix_seed=request.prefix_seed, as_path_seed=request.as_path_seed,
    )


@lab_router.post("/speakers/{node}/withdraw")
async def withdraw_speaker_route(
    key: str,
    node: str,
    request: WithdrawRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Withdraw a prefix from a Speaker, or everything it announced (authenticated, any role).

    The body carries ``prefix`` or ``all: true``, and optionally
    ``neighbor``. ``all`` is ExaBGP's ``clear adj-rib out``: it withdraws the
    Lab Example's routes along with anything announced since.
    """
    return await registry.withdraw_route(
        key,
        node, request.prefix, request.all, neighbor=request.neighbor,
    )


@lab_router.get("/rpki/{node}/cas")
async def list_rpki_cas(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """List an RPKI CA's Hosted CAs with their ROAs and ASPAs (authenticated).

    Every answer is read from Krill, so a change made with ``krillc`` in a
    Terminal shows here. 400 names the Node when it is not an RPKI CA; 503 when
    Krill cannot be reached.
    """
    return await registry.list_rpki_cas(key, node)


@lab_router.get("/rpki/{node}/cas/{asn}/roas")
async def get_rpki_roas(
    key: str,
    node: str,
    asn: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return one Hosted CA's ROAs (authenticated).

    400 names the ASN when the RPKI CA holds no Hosted CA for it.
    """
    hosted = await registry.get_rpki_hosted_ca(key, node, asn)
    return {"node": node, "asn": hosted["asn"], "roas": hosted["roas"]}


@lab_router.post("/rpki/{node}/cas/{asn}/roas")
async def upsert_rpki_roa(
    key: str,
    node: str,
    asn: str,
    request: RPKIROAUpsertRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Sign a ROA at a Hosted CA, or edit one (authenticated, any role).

    With ``old`` present the ROA it names is withdrawn and this one signed in
    the same Krill delta, so an edit that changes maxLength is one change. A
    prefix the Hosted CA does not hold is refused with 400 naming the Hosted CA
    and the prefix; a malformed value is refused with 400 before Krill hears of
    it.
    """
    fields = request.model_dump(exclude={"old"})
    if request.old is None:
        return await registry.add_rpki_roa(key, node, asn, fields)
    return await registry.replace_rpki_roa(key, node, asn, request.old.model_dump(), fields)


@lab_router.delete("/rpki/{node}/cas/{asn}/roas")
async def remove_rpki_roa(
    key: str,
    node: str,
    asn: str,
    request: RPKIROAFields,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Withdraw a ROA from a Hosted CA (authenticated, any role).

    The body names the ROA by prefix, maxLength and origin, as Krill matches it.
    """
    return await registry.remove_rpki_roa(key, node, asn, request.model_dump())


@lab_router.get("/rpki/{node}/cas/{asn}/aspas")
async def get_rpki_aspas(
    key: str,
    node: str,
    asn: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return one Hosted CA's ASPAs (authenticated).

    400 names the ASN when the RPKI CA holds no Hosted CA for it.
    """
    hosted = await registry.get_rpki_hosted_ca(key, node, asn)
    return {"node": node, "asn": hosted["asn"], "aspas": hosted["aspas"]}


@lab_router.post("/rpki/{node}/cas/{asn}/aspas")
async def add_rpki_aspa(
    key: str,
    node: str,
    asn: str,
    request: RPKIASPAFields,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Sign an ASPA at a Hosted CA, replacing any it has for the customer (authenticated, any role).

    The customer must be the Hosted CA's own ASN; any other is refused with 400
    before Krill hears of it.
    """
    return await registry.add_rpki_aspa(key, node, asn, request.model_dump())


@lab_router.delete("/rpki/{node}/cas/{asn}/aspas")
async def remove_rpki_aspa(
    key: str,
    node: str,
    asn: str,
    request: RPKIASPARemoveRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Withdraw a customer's ASPA from a Hosted CA (authenticated, any role)."""
    return await registry.remove_rpki_aspa(key, node, asn, request.customer)


@lab_router.get("/rpki/{node}/vrps")
async def get_rpki_vrps(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Read what a Validator serves: its VRPs, ASPAs, refresh times and connected Routers (authenticated).

    Every answer is read from Routinator, so a change Krill publishes shows here
    once the Validator's next run has fetched it. 400 names the Node when it is
    not a Validator; 503 when Routinator is not answering.
    """
    return await registry.get_rpki_vrps(key, node)


@lab_router.get("/dns/nodes")
async def list_dns_nodes(
    key: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """List Nodes offering an Authoritative DNS Service and their hosted Zones.

    Args:
        _user: Authenticated user context.

    Returns:
        Dict with "nodes" list of DNS services and hosted zones.
    """
    return await registry.list_dns_nodes(key)


@lab_router.get("/dns/{node}/zones/{zone}/records")
async def get_dns_records(
    key: str,
    node: str,
    zone: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return the active Resource Records of one hosted zone (authenticated).

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        _user: Authenticated user context.

    Returns:
        Dict with node, zone, and list of records.
    """
    return await registry.get_dns_records(key, node, zone)


@lab_router.post("/dns/{node}/zones/{zone}/records")
async def set_dns_record(
    key: str,
    node: str,
    zone: str,
    request: DNSRecordUpsertRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Add or update a Resource Record in an atomic Knot zone transaction.

    Knot semantic check failures (e.g. CNAME collisions, bad syntax) are
    returned as HTTP 400 with structured error details via the
    DNSValidationError handler in ``create_app``.

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        request: Validated record upsert specification.
        _user: Authenticated user context.

    Returns:
        Dict describing the committed record.
    """
    return await registry.set_dns_record(
        key,
        node,
        zone,
        request.owner,
        request.type,
        request.data,
        request.ttl,
        old_owner=request.old_owner,
        old_type=request.old_type,
        old_data=request.old_data,
    )


@lab_router.delete("/dns/{node}/zones/{zone}/records")
async def unset_dns_record(
    key: str,
    node: str,
    zone: str,
    request: DNSRecordDeleteRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Remove a Resource Record in an atomic Knot zone transaction.

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        request: Validated record deletion specification.
        _user: Authenticated user context.

    Returns:
        Dict describing the removed record.
    """
    return await registry.unset_dns_record(
        key,
        node, zone, request.owner, request.type, request.data,
    )


@lab_router.get("/dns/{node}/zones/{zone}/dnssec")
async def get_zone_dnssec(
    key: str,
    node: str,
    zone: str,
    _user: dict = Depends(get_current_user_with_role),
) -> Dict[str, Any]:
    """Return the DNSSEC state, keys, and DS records of one hosted zone (authenticated).

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        _user: Authenticated user context.

    Returns:
        Dict containing DNSSEC status, NSEC3 settings, key list, and DS records.
    """
    return await registry.get_dnssec_details(key, node, zone)


@lab_router.post("/dns/{node}/zones/{zone}/dnssec/rollover")
async def trigger_dnssec_rollover(
    key: str,
    node: str,
    zone: str,
    request: Optional[DNSSECRolloverRequest] = None,
    _user: dict = Depends(get_current_user_with_role),
) -> Dict[str, Any]:
    """Trigger an on-demand DNSSEC key rollover for a hosted zone.

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        request: Optional rollover specification (defaults to key_type 'zsk').
        _user: Authenticated user context.

    Returns:
        Dict describing rollover status.
    """
    req = request or DNSSECRolloverRequest()
    return await registry.trigger_dnssec_rollover(key, node, zone, req.key_type)


@lab_router.get("/dns/{node}/stats")
async def get_dns_stats(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return live mod-stats query counters for a Node's DNS Service.

    Args:
        node: Serving Node's name.
        _user: Authenticated user context.

    Returns:
        Dict with node and mod-stats counters.
    """
    return await registry.get_dns_stats(key, node)


@lab_router.post("/dns/{node}/stats/reset")
async def reset_dns_stats(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Reset the query statistics baseline for a Node's DNS Service.

    Args:
        node: Serving Node's name.
        _user: Authenticated user context.

    Returns:
        Dict confirming stats reset.
    """
    return await registry.reset_dns_stats(key, node)


@lab_router.get("/dns/{node}/zones/{zone}/status")
async def get_dns_zone_status(
    key: str,
    node: str,
    zone: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return SOA serial and operational status for one hosted zone.

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        _user: Authenticated user context.

    Returns:
        Dict with node, zone, serial, primary_ns, and Knot operational status.
    """
    return await registry.get_dns_zone_status(key, node, zone)


@lab_router.get("/dns/{node}/transfers")
async def get_dns_transfers(
    key: str,
    node: str,
    zone: str = "",
    include_notify: bool = False,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return the Zone Transfers a Node has made and its replication state.

    Read on request from the log Knot already writes; there is no watcher and
    no history kept on our side.

    Args:
        node: Serving Node's name.
        zone: Optional zone apex to narrow the Transfer Log to.
        include_notify: Whether to include Notify prompt events in the log.
        _user: Authenticated user context.

    Returns:
        Dict with node, zone, role, primary, transfers and state.
    """
    if include_notify:
        return await registry.get_dns_transfers(
            key,
            node, zone or None, include_notify=True
        )
    return await registry.get_dns_transfers(key, node, zone or None)


@lab_router.post("/dns/{node}/zones/{zone}/refresh")
async def refresh_dns_zone(
    key: str,
    node: str,
    zone: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Ask a Secondary Zone whether its Primary's Serial has moved.

    A Zone whose Serial has not advanced transfers nothing at all, which is
    what separates this from the retransfer route.

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        _user: Authenticated user context.

    Returns:
        Dict naming the node, zone, action and Primary.
    """
    return await registry.refresh_dns_zone(key, node, zone)


@lab_router.post("/dns/{node}/zones/{zone}/retransfer")
async def retransfer_dns_zone(
    key: str,
    node: str,
    zone: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Ask a Secondary Zone to take a full copy regardless of its Serial.

    Args:
        node: Serving Node's name.
        zone: Zone apex domain name.
        _user: Authenticated user context.

    Returns:
        Dict naming the node, zone, action and Primary.
    """
    return await registry.retransfer_dns_zone(key, node, zone)


@lab_router.get("/nodes/{node}/resolver/cache")
async def get_resolver_cache(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return everything a Resolver holds in cache, with its query counters.

    Args:
        node: The resolving Node's name.
        _user: Authenticated user context.

    Returns:
        Dict with node, cache entries in stable order, and metrics.
    """
    return await registry.get_resolver_cache(key, node)


@lab_router.post("/nodes/{node}/resolver/cache/flush")
async def flush_resolver_cache(
    key: str,
    node: str,
    request: ResolverFlushRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Evict cached entries so the next query walks the hierarchy again.

    Args:
        node: The resolving Node's name.
        request: What to evict, and at which scope.
        _user: Authenticated user context.

    Returns:
        Dict describing what was flushed and Unbound's confirmation line.
    """
    return await registry.flush_resolver_cache(
        key,
        node, request.target, request.scope,
    )


@lab_router.get("/nodes/{node}/resolver/access")
async def get_resolver_access(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Return the Access Control rules a Resolver is enforcing.

    Args:
        node: The resolving Node's name.
        _user: Authenticated user context.

    Returns:
        Dict with node and the rules, in declaration order.
    """
    return await registry.get_resolver_access(key, node)


@lab_router.put("/nodes/{node}/resolver/access")
async def set_resolver_access(
    key: str,
    node: str,
    request: ResolverAccessRequest,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Replace a Resolver's Access Control rules and apply them hot.

    This is Exercise Configuration: a Learner authors it at
    runtime, it takes effect without dropping the cache the Cache Inspector is
    showing, and it resets when the Lab stops. The rules now in force are
    broadcast to every open browser session as ``RESOLVER_ACCESS_UPDATED``.

    Args:
        node: The resolving Node's name.
        request: The policy to put in force, in declaration order.
        _user: Authenticated user context.

    Returns:
        Dict with node and the rules now in force.
    """
    return await registry.set_resolver_access(
        key,
        node, [rule.model_dump() for rule in request.rules],
    )


# ── Switch Management Endpoints ──────────────────────────────────────────────


def _switch_http_error(exc: ValueError) -> HTTPException:
    """Map a registry ValueError on a Switch route to its HTTP error.

    Args:
        exc: The error the registry raised.

    Returns:
        A 404 when the message says something was not found, else a 400.
    """
    msg = str(exc)
    code = status.HTTP_404_NOT_FOUND if "not found" in msg.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=msg)


@lab_router.get("/switches/{switch}/vlans")
async def get_switch_vlans(
    key: str,
    switch: str,
    _user: dict = Depends(get_current_user_with_role),
) -> dict:
    """Return VLAN configurations for all ports on a switch (authenticated, any role)."""
    try:
        return await registry.get_switch_vlans(key, switch)
    except ValueError as exc:
        raise _switch_http_error(exc) from exc


@lab_router.post("/switches/{switch}/ports/{port}/vlan")
async def set_switch_port_vlan(
    key: str,
    switch: str,
    port: str,
    request: SwitchPortVlanRequest,
    _user: dict = Depends(get_current_user_with_role),
) -> dict:
    """Configure a switch port's VLAN mode and membership, broadcasting topology update."""
    raw_vlan = request.vlan if request.vlan is not None else request.tag
    try:
        return await registry.set_switch_port_vlan(
            key,
            switch,
            port,
            request.mode,
            vlan=raw_vlan,
            trunks=request.trunks,
            native=request.native,
        )
    except ValueError as exc:
        raise _switch_http_error(exc) from exc


@lab_router.get("/capture/interfaces/{node}")
async def get_capture_interfaces(
    key: str,
    node: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """List every Interface of a Node that may be captured (authenticated).

    Includes Interfaces whose Link is not drawn, which the Link menu cannot
    reach.
    """
    return {"interfaces": await registry.capture_interfaces(key, node)}


@lab_router.get("/capture/pcap")
async def get_capture_pcap(
    key: str,
    node: str,
    interface: str,
    capture_filter: str = Query("", alias="filter"),
    _user: dict = Depends(get_current_user_with_role)
) -> StreamingResponse:
    """Download a Packet Capture's pcap (authenticated).

    The ring's files are complete pcapng sections, served oldest first as
    one file, which Wireshark reads as one capture.  They live in the Lab
    directory and go when the Lab stops.
    """
    files = await registry.capture_pcap(key, node, interface, capture_filter)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pcap has been written for {interface} on {node} yet",
        )

    def sections() -> Iterator[bytes]:
        for path in files:
            try:
                with open(path, "rb") as section:
                    while chunk := section.read(65536):
                        yield chunk
            except FileNotFoundError:
                # The ring moved on while this was being served.
                continue

    return StreamingResponse(
        sections(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{key}-{node}-{interface}.pcapng"'},
    )


@lab_router.get("/nat-table")
async def get_nat_table(
    key: str,
    _user: dict = Depends(get_current_user_with_role)
) -> dict:
    """Every translating Router's NAT Table (authenticated).

    Polled by the NAT Table pane.  Each table is read inside its Router,
    so the Internet Gateway's and another Lab's entries never appear.
    """
    return {"tables": await registry.nat_table(key)}


@lab_router.get("/switches/{switch}/mac-table")
async def get_switch_mac_table(
    key: str, switch: str, _user: dict = Depends(get_current_user_with_role),
) -> dict:
    """Return the Switch's MAC address table for an authenticated viewer."""
    try:
        return await registry.get_switch_mac_table(key, switch)
    except ValueError as exc:
        raise _switch_http_error(exc) from exc


@lab_router.post("/switches/{switch}/mac-table/flush")
async def flush_switch_mac_table(
    key: str, switch: str, _user: dict = Depends(get_current_user_with_role),
) -> dict:
    """Flush learned MAC addresses and return the Switch's current table."""
    try:
        return await registry.flush_switch_mac_table(key, switch)
    except ValueError as exc:
        raise _switch_http_error(exc) from exc


@lab_router.get("/switches/{switch}/spanning-tree")
async def get_switch_spanning_tree(
    key: str, switch: str, _user: dict = Depends(get_current_user_with_role),
) -> dict:
    """Return the Switch's spanning tree for an authenticated viewer."""
    try:
        return await registry.get_switch_spanning_tree(key, switch)
    except ValueError as exc:
        raise _switch_http_error(exc) from exc


router.include_router(lab_router)
