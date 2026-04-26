"""
Services package

Modules:
- video_streaming: Video URL extraction
"""

from .video_streaming import get_video_info, get_stream_url

__all__ = ['get_video_info', 'get_stream_url']
