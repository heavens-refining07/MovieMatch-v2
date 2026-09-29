from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class MovieItem(BaseModel):
    id: int
    title: str
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    release_date: Optional[str] = ''
    release_year: Optional[str] = ''
    vote_average: float = 0.0
    vote_count: int = 0
    overview: str = ''
    genres: List[str] = Field(default_factory=list)
    providers: List[str] = Field(default_factory=list)
    runtime: Optional[int] = None

class Participant(BaseModel):
    id: str
    name: str
    is_host: bool = False
    avatar_color: str = '#8b5cf6'
    voted_count: int = 0
    total_count: int = 0
    has_finished: bool = False
    is_connected: bool = True

class RoomFilter(BaseModel):
    region: str = 'IN'
    provider_ids: List[int] = Field(default_factory=list)
    genre_ids: List[int] = Field(default_factory=list)
    min_rating: float = 6.0
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    card_count: int = 15

class LeaderboardItem(BaseModel):
    movie: MovieItem
    likes: int
    dislikes: int
    total_votes: int
    percentage: int
    voters: List[str] = Field(default_factory=list)

class GameResult(BaseModel):
    winner: MovieItem
    is_tie_break: bool = False
    tied_candidates: List[MovieItem] = Field(default_factory=list)
    total_voters: int = 0
    max_likes: int = 0
    leaderboard: List[LeaderboardItem] = Field(default_factory=list)
