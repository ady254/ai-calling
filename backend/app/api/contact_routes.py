from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from uuid import UUID
import csv
import io

from app.schemas.contact_schema import ContactCreate, ContactUpdate, ContactOut
from app.services.contact_service import (
    create_contact, get_contacts_by_business, get_contact,
    update_contact, delete_contact
)
from app.models.contact import Contact
from app.models.campaign_contact import CampaignContact
from app.models.campaign import Campaign
from app.models.call_log import CallLog
from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business
from app.dependencies.ownership import get_owned_contact
from app.core.rate_limit import limiter

router = APIRouter()

# Fix #18: Maximum allowed CSV upload size (5 MB)
MAX_CSV_BYTES = 5 * 1024 * 1024

# CRM sales funnel stages, in order (separate from per-call ContactStatus).
PIPELINE_STAGES = ["new", "contacted", "interested", "qualified", "booked", "won"]
PIPELINE_LABELS = {
    "new": "New", "contacted": "Contacted", "interested": "Interested",
    "qualified": "Qualified", "booked": "Booked", "won": "Won", "lost": "Lost",
}


@router.post("", response_model=ContactOut)
@router.post("/", response_model=ContactOut)
async def create_contact_route(
    data: ContactCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    data.business_id = business.id
    return await create_contact(db, data)


@router.get("", response_model=list[ContactOut])
@router.get("/", response_model=list[ContactOut])
async def list_contacts_route(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    return await get_contacts_by_business(db, business.id, skip, limit)


# ── AI CRM read endpoints ─────────────────────────────────────────────
# NOTE: these static paths are declared BEFORE `/{contact_id}` so FastAPI
# does not try to parse "crm"/"kpis"/"pipeline" as a UUID.

@router.get("/crm")
async def list_contacts_crm(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Contacts enriched for the CRM table (latest call, campaign, AI insights)."""
    business = await get_user_business(db, user_id)
    contacts = await get_contacts_by_business(db, business.id, skip=0, limit=200)

    # Latest call log per contact (single ordered scan → first seen = latest).
    last_call: dict[str, CallLog] = {}
    try:
        rows = (
            await db.execute(
                select(CallLog)
                .where(CallLog.business_id == business.id)
                .order_by(CallLog.created_at.desc())
            )
        ).scalars().all()
        for r in rows:
            if r.contact_id and str(r.contact_id) not in last_call:
                last_call[str(r.contact_id)] = r
    except Exception:
        last_call = {}

    # Campaign name per contact.
    campaign_of: dict[str, str] = {}
    try:
        crows = (
            await db.execute(
                select(CampaignContact.contact_id, Campaign.name)
                .join(Campaign, Campaign.id == CampaignContact.campaign_id)
                .where(Campaign.business_id == business.id)
            )
        ).all()
        for cid, cname in crows:
            campaign_of.setdefault(str(cid), cname)
    except Exception:
        campaign_of = {}

    result = []
    for c in contacts:
        cid = str(c.id)
        cl = last_call.get(cid)
        insights = c.ai_insights or {}
        result.append({
            "id": cid,
            "name": c.name,
            "company": c.company or "",
            "phone": c.phone_number,
            "email": c.email or "",
            "industry": c.industry or "—",
            "leadScore": c.lead_score or 0,
            "status": c.pipeline_stage or "new",
            "lastContact": cl.created_at.isoformat() if cl and cl.created_at else None,
            "assignedAgent": insights.get("assigned_agent", "Unassigned"),
            "nextAction": (cl.follow_up if cl and cl.follow_up else "First call"),
            "tags": [t.strip() for t in (c.tags or "").split(",") if t.strip()],
            "assignedCampaign": campaign_of.get(cid, "Unassigned"),
            "lastCallSummary": (cl.summary if cl and cl.summary else "No calls yet."),
            "sentiment": insights.get("sentiment", "Neutral"),
            "nextFollowUp": insights.get("next_follow_up", "Not scheduled"),
            "notes": ([{"id": "n1", "text": c.notes, "time": ""}] if c.notes else []),
            "conversionProbability": insights.get("conversion_probability", c.lead_score or 0),
            "aiRecommendations": insights.get("recommendations", []),
        })
    return result


@router.get("/kpis")
async def contacts_kpis(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Six headline metrics for the CRM KPI row."""
    business = await get_user_business(db, user_id)
    base = select(func.count()).select_from(Contact).where(Contact.business_id == business.id)

    async def count(*conds):
        stmt = base
        for cond in conds:
            stmt = stmt.where(cond)
        return (await db.execute(stmt)).scalar() or 0

    total = await count()
    qualified = await count(Contact.lead_score >= 75)
    customers = await count(Contact.pipeline_stage == "won")
    appointments = await count(Contact.pipeline_stage == "booked")
    followups = await count(Contact.pipeline_stage.in_(["interested", "qualified"]))
    avg = (
        await db.execute(
            select(func.avg(Contact.lead_score)).where(Contact.business_id == business.id)
        )
    ).scalar()
    avg_score = round(float(avg)) if avg is not None else 0

    return [
        {"id": "total", "label": "Total Contacts", "value": f"{total:,}", "hint": "Across all lists", "icon": "total"},
        {"id": "qualified", "label": "Qualified Leads", "value": str(qualified), "hint": "Lead score > 75", "icon": "qualified"},
        {"id": "followups", "label": "Follow-ups Due", "value": str(followups), "hint": "Interested & qualified", "icon": "followups"},
        {"id": "appointments", "label": "Appointments", "value": str(appointments), "hint": "Booked", "icon": "appointments"},
        {"id": "customers", "label": "Customers", "value": str(customers), "hint": "Won", "icon": "customers"},
        {"id": "score", "label": "Average Lead Score", "value": str(avg_score), "hint": "Across contacts", "icon": "score"},
    ]


@router.get("/pipeline")
async def contacts_pipeline(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Contact counts per funnel stage for the pipeline overview."""
    business = await get_user_business(db, user_id)
    rows = (
        await db.execute(
            select(Contact.pipeline_stage, func.count())
            .where(Contact.business_id == business.id)
            .group_by(Contact.pipeline_stage)
        )
    ).all()
    counts = {stage: cnt for stage, cnt in rows}
    return [
        {"id": s, "label": PIPELINE_LABELS[s], "count": int(counts.get(s, 0))}
        for s in PIPELINE_STAGES
    ]


@router.get("/{contact_id}", response_model=ContactOut)
async def get_contact_route(
    contact: Contact = Depends(get_owned_contact),
):
    return contact


@router.put("/{contact_id}", response_model=ContactOut)
async def update_contact_route(
    contact_id: UUID,
    data: ContactUpdate,
    contact: Contact = Depends(get_owned_contact),
    db: AsyncSession = Depends(get_db),
):
    return await update_contact(db, contact_id, data)


@router.delete("/{contact_id}")
async def delete_contact_route(
    contact_id: UUID,
    contact: Contact = Depends(get_owned_contact),
    db: AsyncSession = Depends(get_db),
):
    success = await delete_contact(db, contact_id)
    return {"success": success}


@router.post("/import")
@limiter.limit("10/minute")
async def import_contacts_csv(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)

    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    # Fix #18: Enforce a file size limit before reading the whole file into memory.
    # A missing/None content_length means we read up to the limit + 1 byte to detect oversize.
    contents = await file.read(MAX_CSV_BYTES + 1)
    if len(contents) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {MAX_CSV_BYTES // (1024 * 1024)} MB.",
        )

    # Columns consumed directly by the Contact model — anything else in the
    # CSV (e.g. doctor_name, appointment_date, department, mrn) is preserved
    # as a per-contact variable in custom_fields for campaign personalization.
    KNOWN_COLUMNS = {"name", "phone_number", "email", "company", "tags"}

    try:
        csv_data = contents.decode("utf-8")
        reader = csv.DictReader(io.StringIO(csv_data))

        contacts_to_add: list[ContactCreate] = []
        for row in reader:
            name = row.get("name", "").strip()
            phone = row.get("phone_number", "").strip()
            if name and phone:
                custom_fields = {
                    key.strip(): value.strip()
                    for key, value in row.items()
                    if key and key.strip().lower() not in KNOWN_COLUMNS and value and value.strip()
                }
                contacts_to_add.append(
                    ContactCreate(
                        business_id=business.id,
                        name=name,
                        phone_number=phone,
                        email=row.get("email", "").strip() or None,
                        company=row.get("company", "").strip() or None,
                        tags=row.get("tags", "").strip() or None,
                        custom_fields=custom_fields or None,
                    )
                )

        # Fix #19: Batch all inserts — flush per row, single commit at the end.
        # Previously each create_contact() committed individually (N round-trips).
        imported_count = 0
        for contact_data in contacts_to_add:
            await create_contact(db, contact_data, auto_commit=False)
            imported_count += 1

        await db.commit()

        return {"success": True, "imported": imported_count}

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Error processing CSV: {str(e)}")