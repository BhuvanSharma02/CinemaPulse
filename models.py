from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    poster_url = db.Column(db.String(500), nullable=False)
    
    # Relationship to access all reviews for this movie
    feedbacks = db.relationship('Feedback', backref='movie', lazy=True, cascade="all, delete-orphan")

    @property
    def average_rating(self):
        if not self.feedbacks:
            return 0.0
        total = sum(f.rating for f in self.feedbacks)
        return total / len(self.feedbacks)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False) # Linked to Movie table
    rating = db.Column(db.Integer, nullable=False)
    review = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('feedbacks', lazy=True))