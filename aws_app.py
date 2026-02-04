import boto3
import uuid
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, flash, request, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from botocore.exceptions import ClientError
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'

# --- AWS Configuration ---
AWS_REGION = "us-east-1" 
DYNAMO_TABLE_USERS = "CinemaPulseUsers"
DYNAMO_TABLE_FEEDBACK = "CinemaPulseFeedback"
DYNAMO_TABLE_MOVIES = "CinemaPulseMovies"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:CinemaPulseAlerts" 

# Initialize Boto3
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
sns_client = boto3.client('sns', region_name=AWS_REGION)

# Tables
users_table = dynamodb.Table(DYNAMO_TABLE_USERS)
feedback_table = dynamodb.Table(DYNAMO_TABLE_FEEDBACK)
movies_table = dynamodb.Table(DYNAMO_TABLE_MOVIES)

# --- Login Manager Setup ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, username, password, is_admin=False):
        self.id = username # Using username as the unique ID for Flask-Login
        self.username = username
        self.password = password
        self.is_admin = is_admin

    @staticmethod
    def get(username):
        try:
            response = users_table.get_item(Key={'username': username})
            if 'Item' in response:
                item = response['Item']
                return User(item['username'], item['password'], item.get('is_admin', False))
        except Exception as e:
            print(f"Error fetching user: {e}")
        return None

@login_manager.user_loader
def load_user(username):
    return User.get(username)

# --- Helper Functions ---
def get_all_movies():
    try:
        # Get all movies
        movies_resp = movies_table.scan()
        movies = movies_resp.get('Items', [])
        
        # Get all feedbacks to calculate ratings
        # In a real app, we would store avg_rating on the Movie item or use a GSI
        feedback_resp = feedback_table.scan()
        feedbacks = feedback_resp.get('Items', [])
        
        # Map ratings to movies
        movie_ratings = {}
        for f in feedbacks:
            m_title = f.get('movie_title')
            if m_title:
                if m_title not in movie_ratings:
                    movie_ratings[m_title] = []
                movie_ratings[m_title].append(float(f['rating']))
                
        for m in movies:
            title = m['title']
            if title in movie_ratings:
                ratings = movie_ratings[title]
                m['average_rating'] = sum(ratings) / len(ratings)
            else:
                m['average_rating'] = 0.0
                
        return movies
    except ClientError as e:
        print(f"Error scanning movies: {e}")
        return []

def get_movie_by_id(movie_title):
    try:
        response = movies_table.get_item(Key={'title': movie_title})
        movie = response.get('Item')
        if movie:
             # Calculate rating for single movie
            try:
                fb_resp = feedback_table.scan(
                    FilterExpression=boto3.dynamodb.conditions.Attr('movie_title').eq(movie_title)
                )
                fbs = fb_resp.get('Items', [])
                if fbs:
                    total = sum(float(f['rating']) for f in fbs)
                    movie['average_rating'] = total / len(fbs)
                else:
                    movie['average_rating'] = 0.0
            except Exception:
                movie['average_rating'] = 0.0
        return movie
    except ClientError as e:
        print(f"Error getting movie: {e}")
        return None

# --- Seed Data (Optional: Run once to populate DynamoDB) ---
def seed_movies_dynamo():
    # Only seed if table is empty (simple check)
    if get_all_movies():
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
                "desc": "A thief who steals corporate secrets through the use of dream-sharing technology.",
                "poster": "https://image.tmdb.org/t/p/w500/xlaY2zyzMfkhk0HSC5VUwzoZPU1.jpg"
            },
            {
                "title": "The Dark Knight",
                "desc": "Batman must accept one of the greatest psychological and physical tests of his ability to fight injustice.",
                "poster": "https://image.tmdb.org/t/p/w500/qJ2tW6WMUDux911r6m7haRef0WH.jpg"
            },
            {
                "title": "Interstellar",
                "desc": "A team of explorers travel through a wormhole in space in an attempt to ensure humanity's survival.",
                "poster": "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg"
            }
        ]

    for m in movies:
        try:
            movies_table.put_item(Item=m)
        except Exception as e:
            print(f"Error seeding movie {m['title']}: {e}")
    print("DynamoDB Movies seeded.")

# --- Routes ---

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('movies'))
    
    # Fetch movies from DynamoDB for display
    movies = get_all_movies()
    return render_template('home.html', movies=movies)

@app.route('/movies')
def movies():
    movies = get_all_movies()
    return render_template('movies.html', movies=movies)

@app.route('/movie/import/<int:tmdb_id>')
@login_required
def import_tmdb_movie(tmdb_id):
    api_key = os.environ.get('TMDB_API_KEY')
    if not api_key:
        flash('API configuration error.', 'danger')
        return redirect(url_for('home'))

    try:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={api_key}&language=en-US"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            title = data.get('title')
            
            # Check if exists locally
            existing = get_movie_by_id(title)
            if existing:
                return redirect(url_for('movie_details', movie_title=title))

            # Create new item
            item = {
                "title": title,
                "desc": data.get('overview', ''),
                "poster": f"https://image.tmdb.org/t/p/w500{data.get('poster_path')}" if data.get('poster_path') else ''
            }
            movies_table.put_item(Item=item)
            return redirect(url_for('movie_details', movie_title=title))
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

    # 1. Search DynamoDB (Simple filtering)
    local_movies = get_all_movies()
    # Filter locally
    filtered_locals = [m for m in local_movies if query.lower() in m['title'].lower()]
    
    for m in filtered_locals:
        results.append({
            'title': m['title'],
            'desc': m['desc'],
            'poster': m['poster'],
            'rating': m.get('average_rating', 0.0),
            'view_url': url_for('movie_details', movie_title=m['title']),
            'source': 'local'
        })
        seen_titles.add(m['title'].lower())

    # 2. Search TMDB API
    api_key = os.environ.get('TMDB_API_KEY')
    
    if api_key:
        try:
            url = f"https://api.themoviedb.org/3/search/movie?api_key={api_key}&query={query}&language=en-US&page=1"
            response = requests.get(url)
            if response.status_code == 200:
                tmdb_results = response.json().get('results', [])
                for m in tmdb_results:
                    if not m.get('poster_path') or m['title'].lower() in seen_titles:
                        continue
                    
                    # Double check if it exists in ALL local movies to avoid duplicates
                    all_local_titles = {mov['title'].lower() for mov in local_movies}
                    if m['title'].lower() in all_local_titles:
                         # It exists locally but wasn't in filtered_locals (shouldn't happen with simple contains, but just in case)
                         # Add as local result if relevant? 
                         # Actually if it matches query it should be in filtered_locals.
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
    
    # Convert to JSON (DynamoDB items are dicts already)
    return jsonify(results)

@app.route('/movie/<path:movie_title>')
def movie_details(movie_title):
    # Retrieve movie details
    movie = get_movie_by_id(movie_title)
    if not movie:
        abort(404)
        
    # Get reviews for this movie
    # Note: efficient querying requires a GSI on 'movie_title', here we Scan for simplicity in prototype
    try:
        response = feedback_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('movie_title').eq(movie_title)
        )
        reviews = response.get('Items', [])
        # Sort manually since Scan doesn't sort
        reviews.sort(key=lambda x: x['timestamp'], reverse=True)
    except Exception as e:
        print(f"Error fetching reviews: {e}")
        reviews = []

    return render_template('movie_details.html', movie=movie, reviews=reviews)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check if user exists
        if User.get(username):
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
            
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        is_admin = (username == 'admin')

        try:
            users_table.put_item(Item={
                'username': username,
                'password': hashed_pw,
                'is_admin': is_admin
            })
            
            # --- AWS SNS: Notify on Account Creation ---
            try:
                sns_client.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Message=f"New User Registered!\nUsername: {username}\nTime: {datetime.utcnow().isoformat()}",
                    Subject="CinemaPulse: New Account Created"
                )
                print(f"[AWS SNS] Registration alert sent for '{username}'")
            except Exception as e:
                print(f"[AWS SNS] Failed to send registration alert: {e}")

            user = User(username, hashed_pw, is_admin)
            login_user(user)
            return redirect(url_for('movies'))
        except Exception as e:
            flash(f'Error creating account: {e}', 'danger')
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.get(username)
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
        user = User.get(username)
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
    try:
        if current_user.is_admin:
            response = feedback_table.scan()
            feedbacks = response.get('Items', [])
            feedbacks.sort(key=lambda x: x['timestamp'], reverse=True)
            return render_template('dashboard_admin.html', feedbacks=feedbacks)
        else:
            response = feedback_table.scan(
                FilterExpression=boto3.dynamodb.conditions.Attr('user_id').eq(current_user.id)
            )
            user_feedbacks = response.get('Items', [])
            user_feedbacks.sort(key=lambda x: x['timestamp'], reverse=True)
            return render_template('dashboard_user.html', feedbacks=user_feedbacks)
    except Exception as e:
        flash(f"Error loading dashboard: {e}", "danger")
        return redirect(url_for('home'))

@app.route('/submit_feedback', methods=['POST'])
@login_required
def submit_feedback():
    movie_title = request.form.get('movie_title')
    rating = int(request.form.get('rating'))
    review = request.form.get('review')
    
    feedback_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()
    
    item = {
        'id': feedback_id,
        'user_id': current_user.id,
        'movie_title': movie_title,
        'rating': rating,
        'review': review,
        'timestamp': timestamp
    }
    
    try:
        feedback_table.put_item(Item=item)
        flash('Feedback submitted successfully!', 'success')
        
    except Exception as e:
        flash(f'Error submitting feedback: {e}', 'danger')

    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    # Uncomment this to seed movies if you have created the table 'CinemaPulseMovies'
    # seed_movies_dynamo() 
    app.run(host='0.0.0.0', port=5000, debug=True)
