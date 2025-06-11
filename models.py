from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from flask_sqlalchemy import SQLAlchemy

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class Voter(db.Model):
    __tablename__ = 'voters'
    
    id = Column(Integer, primary_key=True)
    voter_id = Column(String(9), unique=True, nullable=False)
    voted_at = Column(DateTime, default=func.now())
    party = Column(String(50), nullable=True)
    name = Column(String(100), nullable=True)
    
    def __repr__(self):
        return f'<Voter {self.voter_id}>'

class Vote(db.Model):
    __tablename__ = 'votes'
    
    id = Column(Integer, primary_key=True)
    party = Column(String(50), nullable=False)
    timestamp = Column(DateTime, default=func.now())
    
    def __repr__(self):
        return f'<Vote for {self.party}>'

class Admin(db.Model):
    __tablename__ = 'admins'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    
    def __repr__(self):
        return f'<Admin {self.username}>'
        
class ElectionStatus(db.Model):
    __tablename__ = 'election_status'
    
    id = Column(Integer, primary_key=True)
    is_open = Column(Boolean, default=True)
    message = Column(Text, nullable=True)
    countdown_end = Column(DateTime, nullable=True)
    winner = Column(String(50), nullable=True)
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f'<ElectionStatus open={self.is_open}>'
        
class Party(db.Model):
    __tablename__ = 'parties'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    image = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=func.now())
    
    def __repr__(self):
        return f'<Party {self.name}>'
        
class PartyMember(db.Model):
    __tablename__ = 'party_members'
    
    id = Column(Integer, primary_key=True)
    party_name = Column(String(100), nullable=False)
    name = Column(String(100), nullable=False)
    position = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=func.now())
    
    def __repr__(self):
        return f'<PartyMember {self.name} ({self.position})>'
        
class SiteSettings(db.Model):
    __tablename__ = 'site_settings'
    
    id = Column(Integer, primary_key=True)
    site_title = Column(String(100), default="MUSLIM BHADALA JAMAAT")
    site_subtitle = Column(String(100), default="CHAIRMAN ELECTIONS 2025")
    logo_path = Column(String(255), default="images/main-logo.svg")
    theme_color = Column(String(7), default="#0d6efd")  # Default Bootstrap primary blue
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f'<SiteSettings {self.id}>'
        
class MusicSettings(db.Model):
    __tablename__ = 'music_settings'
    
    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, default=False)
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f'<MusicSettings enabled={self.enabled}>'
