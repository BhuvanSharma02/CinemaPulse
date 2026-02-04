from flask import Flask, render_template, redirect, url_for, flash, request, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Feedback, Movie
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cinemapulse.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

# --- Database Seeder ---
def seed_movies():
    if Movie.query.first():
        return

    api_key = os.environ.get('TMDB_API_KEY')
    movies = []

    if api_key:
        try:
            url = f"https://api.themoviedb.org/3/movie/popular?api_key={api_key}&language=en-US&page=1"
            response = requests.get(url)
            if response.status_code == 200:
                results = response.json().get('results', [])
                for m in results[:10]:  # Limit to top 10
                    movies.append({
                        "title": m['title'],
                        "desc": m['overview'],
                        "poster": f"https://image.tmdb.org/t/p/w500{m['poster_path']}"
                    })
            else:
                print(f"Failed to fetch movies from TMDB: {response.status_code}")
        except Exception as e:
            print(f"Error fetching from TMDB: {e}")
    
    if not movies:
        print("Using hardcoded fallback movies.")
        movies = [
            {
                "title": "Inception",
                "desc": "A thief who steals corporate secrets through the use of dream-sharing technology is given the inverse task of planting an idea into the mind of a C.E.O.",
                "poster": "https://image.tmdb.org/t/p/w500/xlaY2zyzMfkhk0HSC5VUwzoZPU1.jpg"
            },
            {
                "title": "The Dark Knight",
                "desc": "When the menace known as the Joker wreaks havoc and chaos on the people of Gotham, Batman must accept one of the greatest psychological and physical tests of his ability to fight injustice.",
                "poster": "https://image.tmdb.org/t/p/w500/qJ2tW6WMUDux911r6m7haRef0WH.jpg"
            },
            {
                "title": "Interstellar",
                "desc": "A team of explorers travel through a wormhole in space in an attempt to ensure humanity's survival.",
                "poster": "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg"
            }
        ]

    for m in movies:
        new_movie = Movie(title=m['title'], description=m['desc'], poster_url=m['poster'])
        db.session.add(new_movie)
    
    db.session.commit()
    print("Database seeded with movies.")

# --- Routes ---

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('movies'))
    movies = Movie.query.all()
    return render_template('home.html', movies=movies)

@app.route('/movies')
def movies():
    movies = Movie.query.all()
    return render_template('movies.html', movies=movies)

@app.route('/movie/tmdb/<int:tmdb_id>')
@login_required
def import_tmdb_movie(tmdb_id):
    api_key = os.environ.get('TMDB_API_KEY')
    if not api_key:
        flash('API configuration error.', 'danger')
        return redirect(url_for('home'))

    try:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={api_key}&language=en-US"
        headers = {'User-Agent': 'CinemaPulse/1.0'}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            title = data.get('title')
            
            # Check if exists locally
            existing = Movie.query.filter_by(title=title).first()
            if existing:
                return redirect(url_for('movie_details', movie_id=existing.id))

            # Create new
            new_movie = Movie(
                title=title,
                description=data.get('overview', ''),
                poster_url=f"https://image.tmdb.org/t/p/w500{data.get('poster_path')}" if data.get('poster_path') else ''
            )
            db.session.add(new_movie)
            db.session.commit()
            return redirect(url_for('movie_details', movie_id=new_movie.id))
        else:
            flash('Movie not found on TMDB.', 'warning')
            return redirect(url_for('home'))
    except Exception as e:
        print(f"Error importing movie: {e}")
        flash('Error importing movie.', 'danger')
        return redirect(url_for('home'))

def perform_search_logic(query):
    if not query:
        return []

    results = []
    seen_titles = set()

    # 1. Search Local Database
    local_movies = Movie.query.filter(Movie.title.ilike(f'%{query}%')).all()
    for m in local_movies:
        results.append({
            'title': m.title,
            'desc': m.description,
            'poster': m.poster_url,
            'rating': m.average_rating,
            'view_url': url_for('movie_details', movie_id=m.id),
            'source': 'local'
        })
        seen_titles.add(m.title.lower())

    # 2. Search TMDB API
    api_key = os.environ.get('TMDB_API_KEY')
    
    if api_key:
        try:
            url = f"https://api.themoviedb.org/3/search/movie?api_key={api_key}&query={query}&language=en-US&page=1"
            headers = {'User-Agent': 'CinemaPulse/1.0'}
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                tmdb_results = response.json().get('results', [])
                for m in tmdb_results:
                    if not m.get('poster_path') or m['title'].lower() in seen_titles:
                        continue
                    
                    results.append({
                        'title': m['title'],
                        'desc': m['overview'],
                        'poster': f"https://image.tmdb.org/t/p/w500{m['poster_path']}",
                        'rating': 0.0,
                        'view_url': url_for('import_tmdb_movie', tmdb_id=m['id']),
                        'source': 'tmdb'
                    })
                    seen_titles.add(m['title'].lower())
        except Exception as e:
            print(f"Error searching TMDB: {e}")

    return results

@app.route('/search')
def search():
    query = request.args.get('q')
    if not query:
        return redirect(url_for('movies'))
    
    results = perform_search_logic(query)
    return render_template('movies.html', movies=results, search_query=query)

@app.route('/api/search')
def api_search():
    query = request.args.get('q')
    results = perform_search_logic(query)
    return jsonify(results)

@app.route('/movie/<int:movie_id>')
def movie_details(movie_id):
    movie = Movie.query.get_or_404(movie_id)
    # Get reviews for this movie, newest first
    reviews = Feedback.query.filter_by(movie_id=movie_id).order_by(Feedback.timestamp.desc()).all()
    return render_template('movie_details.html', movie=movie, reviews=reviews)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
            
        new_user = User(
            username=username,
            password=generate_password_hash(password, method='pbkdf2:sha256'),
            is_admin=False 
        )
        if username == 'admin':
            new_user.is_admin = True

        db.session.add(new_user)
        db.session.commit()
        
        login_user(new_user)
        return redirect(url_for('movies'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            if user.is_admin:
                return redirect(url_for('dashboard'))
            return redirect(url_for('movies'))
        else:
            flash('Login failed. Check your credentials.', 'danger')
            
    return render_template('login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username')
        user = User.query.filter_by(username=username).first()
        if user:
            flash(f"Password reset instructions have been sent to the email associated with {username}.", "info")
        else:
            flash("Username not found.", "danger")
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        feedbacks = Feedback.query.order_by(Feedback.timestamp.desc()).all()
        return render_template('dashboard_admin.html', feedbacks=feedbacks)
    else:
        # For regular users, maybe show their own reviews
        user_feedbacks = Feedback.query.filter_by(user_id=current_user.id).order_by(Feedback.timestamp.desc()).all()
        return render_template('dashboard_user.html', feedbacks=user_feedbacks)

@app.route('/submit_feedback/<int:movie_id>', methods=['POST'])
@login_required
def submit_feedback(movie_id):
    rating = int(request.form.get('rating'))
    review = request.form.get('review')
    
    new_feedback = Feedback(
        user_id=current_user.id,
        movie_id=movie_id,
        rating=rating,
        review=review
    )
    
    db.session.add(new_feedback)
    db.session.commit()
    
    flash('Review posted successfully!', 'success')
    return redirect(url_for('movie_details', movie_id=movie_id))

@app.route('/delete_review/<int:review_id>', methods=['POST'])
@login_required
def delete_review(review_id):
    if not current_user.is_admin:
        abort(403)
        
    feedback = Feedback.query.get_or_404(review_id)
    db.session.delete(feedback)
    db.session.commit()
    flash('Review deleted.', 'info')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_movies()
    app.run(debug=True, port=5000)