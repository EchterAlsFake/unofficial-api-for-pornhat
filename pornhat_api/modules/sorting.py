from enum import StrEnum


class Sort(StrEnum):
    NEW = ""
    POPULAR = "popular"
    TRENDING = "trending"


class ProfileSort(StrEnum):
    RANK = ""
    VIDEOS = "videos"
    UPDATED = "updated"
    ABC = "abc"
