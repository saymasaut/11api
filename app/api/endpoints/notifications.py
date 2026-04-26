from datetime import datetime

from fastapi import APIRouter

from app.models.schemas import NotificationItem, NotificationResponse

router = APIRouter()


@router.get(
    "/notifications",
    response_model=NotificationResponse,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    tags=["Notifications"],
)
async def get_notifications():
    """
    Get app notifications and announcements.
    In a real app, these would come from a database.
    """
    sample_notifications = [
        NotificationItem(
            id="1",
            title="Welcome to AppHub",
            message="Thank you for using our app! Stay tuned for more features.",
            type="info",
            created_at=datetime(2026, 3, 20),
        ),
        NotificationItem(
            id="2",
            title="AppHub 8.0 Update Released",
            message="Check out the latest update with new features and improvements! Available on AppTeka/AppHub Store and Official Telegram channel",
            type="update",
            created_at=datetime(2026, 4, 23),
        ),
    ]
    return NotificationResponse(
        notifications=sample_notifications,
        total=len(sample_notifications),
    )
