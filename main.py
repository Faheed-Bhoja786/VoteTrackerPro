from flask import Flask, request, render_template, redirect, url_for, session, flash, jsonify, Response
import os
import logging
import hashlib
import secrets
import datetime
import re
import uuid
import json
import pytz
import time
from functools import wraps
from sqlalchemy import func
from werkzeug.utils import secure_filename
from models import db, Voter, Vote, Admin, ElectionStatus, Party, PartyMember, SiteSettings, MusicSettings

# Set Nairobi timezone
NAIROBI_TZ = pytz.timezone('Africa/Nairobi')

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", secrets.token_hex(16))

# Configure database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///election.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
db.init_app(app)

# Ensure static folder exists
if not os.path.exists('static'):
    os.makedirs('static')

# This has been moved to the global_parties declaration further down in the file


# Create a password hash instead of storing in plain text
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# Admin login required decorator
def admin_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)

    return decorated_function


# Get current election status
def get_election_status():
    status = ElectionStatus.query.first()
    if not status:
        status = ElectionStatus(is_open=True)
        db.session.add(status)
        db.session.commit()
    return status

# Get site settings
def get_site_settings():
    settings = SiteSettings.query.first()
    if not settings:
        settings = SiteSettings()
        db.session.add(settings)
        db.session.commit()
    return settings

# Get music settings
def get_music_settings():
    settings = MusicSettings.query.first()
    if not settings:
        settings = MusicSettings(enabled=False)
        db.session.add(settings)
        db.session.commit()
    return settings


@app.route("/", methods=["GET", "POST"])
def index():
    message = ""
    message_class = ""

    # Get election status and site settings
    status = get_election_status()
    site_settings = get_site_settings()

    # Always reload parties from database on index page to ensure we have the latest
    global global_parties
    db_parties = Party.query.all()
    if db_parties:
        # Update global_parties with latest from database
        global_parties = {}
        for party in db_parties:
            global_parties[party.name] = {
                "image": party.image,
                "votes": 0  # Will be updated with vote counts
            }

    # We'll now display the winner directly on the main page instead of redirecting
    # Winner celebration will be handled via JavaScript

    # If voting is closed
    if not status.is_open:
        message = "Voting is currently closed."
        message_class = "error"
    else:
        if request.method == "POST":
            voter_id = request.form["voter_id"].strip()
            voter_name = request.form.get("voter_name", "").strip()
            selected_party = request.form["vote"]

            # Input validation - ensuring ID is exactly 8 or 9 digits using regex
            import re
            if not re.match(r'^\d{8,9}$', voter_id):
                message = "ID must be exactly 8 or 9 digits."
                message_class = "error"
            elif not voter_name:
                message = "Please enter your name."
                message_class = "error"
            else:
                # Check if the voter has already voted
                existing_voter = Voter.query.filter_by(
                    voter_id=voter_id).first()

                if existing_voter:
                    message = "You have already voted."
                    message_class = "error"
                else:
                    try:
                        # Create a new voter record
                        new_voter = Voter(voter_id=voter_id,
                                          party=selected_party,
                                          name=voter_name)
                        db.session.add(new_voter)

                        # Record the vote
                        new_vote = Vote(party=selected_party)
                        db.session.add(new_vote)

                        # Commit changes to the database
                        db.session.commit()

                        # Store success message in session
                        session['vote_message'] = f"Thank you for voting, {voter_name}!"
                        session['message_class'] = "success"

                        # Redirect to prevent form resubmission
                        return redirect(url_for('index'))
                    except Exception as e:
                        db.session.rollback()
                        message = "Error recording vote. Please try again."
                        message_class = "error"

    # Get current vote counts more efficiently with a single query
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()
    for party, count in party_counts:
        vote_counts[party] = count

    # Prepare data for the template
    display_data = {}
    for party in global_parties:
        display_data[party] = {
            "image": global_parties[party]["image"],
            "count": vote_counts.get(party, 0)
        }

    # Calculate countdown end time if it exists
    countdown_html = ""
    if status.countdown_end and status.countdown_end > datetime.datetime.now():
        countdown_date = status.countdown_end.strftime("%B %d, %Y %H:%M:%S")
        countdown_html = f"""
        <div id="countdown" class="countdown">
            <p>Voting closes in <span id="countdown-timer"></span></p>
        </div>
        <script>
            const countdownDate = new Date("{countdown_date}").getTime();

            const countdownTimer = setInterval(function() {{
                const now = new Date().getTime();
                const distance = countdownDate - now;

                const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
                const seconds = Math.floor((distance % (1000 * 60)) / 1000);

                document.getElementById("countdown-timer").innerHTML = minutes + "m " + seconds + "s";

                if (distance < 0) {{
                    clearInterval(countdownTimer);
                    document.getElementById("countdown").innerHTML = "<p>Voting is now closed. Refresh the page.</p>";
                    setTimeout(function() {{
                        window.location.reload();
                    }}, 3000);
                }}
            }}, 1000);
        </script>
        """

    # Get flash message from session
    message = session.pop('vote_message', None)
    message_class = session.pop('message_class', None)

    return render_template("vote.html",
                           votes=display_data,
                           message=message,
                           message_class=message_class,
                           admin_message=status.message,
                           countdown_html=countdown_html,
                           election_open=status.is_open,
                           site_title=site_settings.site_title,
                           site_subtitle=site_settings.site_subtitle,
                           logo_path=site_settings.logo_path,
                           settings=site_settings)


@app.route("/live-results")
def live_results():
    """Display live election results"""
    status = get_election_status()
    site_settings = get_site_settings()
    
    # If a winner is declared, redirect to the winner celebration page
    if status.winner:
        return redirect("/winner-view")
    
    # Add timestamp for cache busting
    now = int(time.time())
    
    # Normal live results page (only shown when no winner)
    return render_template("live_results.html",
                         election_open=status.is_open,
                         winner=None,
                         site_title=site_settings.site_title,
                         site_subtitle=site_settings.site_subtitle,
                         logo_path=site_settings.logo_path,
                         settings=site_settings,
                         now=now)

@app.route("/winner-view")
def winner_view():
    """Display winner celebration view"""
    status = get_election_status()
    site_settings = get_site_settings()
    
    # If no winner, redirect back to live results
    if not status.winner:
        return redirect("/live-results")
    
    # Get winner party data
    winner_party_data = {}
    party_members = {}
    
    # Get data for all parties to find winner
    for party_name in global_parties:
        winner_party_data[party_name] = {
            "image": global_parties[party_name]["image"]
        }
        
        # Get party members
        members = PartyMember.query.filter_by(party_name=party_name).all()
        party_members[party_name] = members
    
    # Add timestamp for cache busting
    now = int(time.time())
    
    return render_template("live_results.html",
                         election_open=status.is_open,
                         winner=status.winner,
                         winner_party_data=winner_party_data,
                         party_members=party_members,
                         site_title=site_settings.site_title,
                         site_subtitle=site_settings.site_subtitle,
                         logo_path=site_settings.logo_path,
                         settings=site_settings,
                         now=now)

@app.route("/admin", methods=["GET"])
def admin_redirect():
    return redirect(url_for('admin_login'))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    site_settings = get_site_settings()
    
    if request.method == "POST":
        password = request.form.get("password")

        # Fixed admin password as requested
        if password == "10032010":
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            error = "Invalid password"

    return render_template("admin.html", 
                          error=error, 
                          login_page=True,
                          settings=site_settings)


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    # Use the exact same vote counting logic as the admin_dashboard_data API
    # to ensure consistency
    
    # Get vote counts for active parties only
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()

    # Only count votes for parties that still exist
    total_valid_votes = 0
    for party, count in party_counts:
        if party in global_parties:
            vote_counts[party] = count
            total_valid_votes += count

    # Get total number of voters
    total_voters = db.session.query(func.count(Voter.id)).scalar()

    # Use valid votes total instead of all votes
    total_votes = total_valid_votes
    vote_data = {}
    
    for party in global_parties:
        vote_count = vote_counts.get(party, 0)
        percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0
        vote_data[party] = {
            "count": vote_count,
            "percentage": round(percentage, 1)
        }

    # Get all voters and their votes for the database view
    all_voters = Voter.query.order_by(Voter.voted_at.desc()).all()

    # Convert all timestamps to Nairobi timezone
    for voter in all_voters:
        if voter.voted_at:
            # Convert UTC time to Nairobi time
            voter.voted_at = voter.voted_at.replace(tzinfo=pytz.UTC).astimezone(NAIROBI_TZ)

    # Get current election status and site settings
    status = get_election_status()
    site_settings = get_site_settings()

    # Calculate the countdown status
    countdown_active = False
    time_remaining = None

    if status.countdown_end and status.countdown_end > datetime.datetime.now():
        countdown_active = True
        time_remaining = status.countdown_end - datetime.datetime.now()
        time_remaining = time_remaining.total_seconds(
        ) // 60  # Convert to minutes

    # Get party members for the party information section
    parties_with_members = {}
    for party_name in global_parties:
        members = PartyMember.query.filter_by(party_name=party_name).all()
        parties_with_members[party_name] = {
            "image": global_parties[party_name]["image"],
            "members": members
        }

    # Add timestamp for cache busting
    now = int(time.time())
    
    return render_template("admin.html",
                           vote_data=vote_data,
                           total_voters=total_voters,
                           total_votes=total_votes,
                           all_voters=all_voters,
                           election_open=status.is_open,
                           admin_message=status.message,
                           countdown_active=countdown_active,
                           time_remaining=time_remaining,
                           winner=status.winner,
                           site_settings=site_settings,
                           parties_with_members=parties_with_members,
                           login_page=False,
                           settings=site_settings,
                           now=now)


@app.route("/admin/reset", methods=["POST"])
@admin_required
def admin_reset():
    confirmation = request.form.get("confirmation")

    if confirmation == "RESET":
        # Delete all votes and voters from the database
        Vote.query.delete()
        Voter.query.delete()

        # Reset the election status
        status = get_election_status()
        status.is_open = True
        status.message = None
        status.countdown_end = None
        status.winner = None

        db.session.commit()

        flash("All votes have been reset successfully", "success")
    else:
        flash("Invalid confirmation text. Votes were not reset.", "error")

    return redirect(url_for('admin_dashboard'))


@app.route("/admin/message", methods=["POST"])
@admin_required
def admin_message():
    message = request.form.get("message", "").strip()

    status = get_election_status()
    status.message = message if message else None
    db.session.commit()

    flash("Message updated successfully", "success")
    return redirect(url_for('admin_dashboard'))


@app.route("/admin/toggle_voting", methods=["POST"])
@admin_required
def toggle_voting():
    status = get_election_status()
    status.is_open = not status.is_open
    db.session.commit()

    state = "opened" if status.is_open else "closed"
    flash(f"Voting has been {state} successfully", "success")
    return redirect(url_for('admin_dashboard'))


@app.route("/admin/countdown", methods=["POST"])
@admin_required
def start_countdown():
    minutes = int(request.form.get("minutes", 5))

    status = get_election_status()
    status.countdown_end = datetime.datetime.now() + datetime.timedelta(
        minutes=minutes)
    db.session.commit()

    flash(f"Countdown timer for {minutes} minutes has been started", "success")
    return redirect(url_for('admin_dashboard'))


@app.route("/admin/declare_winner", methods=["POST"])
@admin_required
def declare_winner():
    confirmation = request.form.get("confirmation")

    if confirmation != "CONFIRM":
        flash("Please type CONFIRM to declare the winner", "error")
        return redirect(url_for('admin_dashboard'))

    # Get vote counts
    vote_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()

    if not vote_counts:
        flash("No votes recorded yet", "error")
        return redirect(url_for('admin_dashboard'))

    # Find winner(s)
    max_votes = max(count for _, count in vote_counts)
    winners = [party for party, count in vote_counts if count == max_votes]

    # Update election status
    status = get_election_status()
    status.winner = " & ".join(winners)
    status.is_open = False
    status.countdown_end = None
    db.session.commit()
    
    # Force a reload of the winner-view page by touching a file
    with open('winner_declared.txt', 'w') as f:
        f.write(f"Winner declared at {datetime.datetime.now()}")

    flash("Winner has been declared!", "success")
    # Redirect back to admin dashboard, don't change user experience
    return redirect(url_for('admin_dashboard'), code=303)


@app.route("/check_winner_status")
def check_winner_status():
    status = get_election_status()

    # Check if countdown has ended
    if status.countdown_end and datetime.datetime.now() >= status.countdown_end:
        if not status.winner:  # Only set winner if not already set
            # Get vote counts
            vote_counts = {}
            party_counts = db.session.query(Vote.party, func.count(
                Vote.id)).group_by(Vote.party).all()
            for party, count in party_counts:
                vote_counts[party] = count

            if vote_counts:
                # Find the highest vote count
                max_votes = max(vote_counts.values())
                # Get all parties with the highest vote count
                winners = [
                    party for party, count in vote_counts.items()
                    if count == max_votes
                ]
                status.winner = " & ".join(
                    winners)  # Join multiple winners with &
                status.is_open = False
                status.countdown_end = None  # Clear countdown
                db.session.commit()

    # Prepare winner details if available
    winner_details = None
    if status.winner:
        winning_parties = status.winner.split(" & ")
        winning_details = []

        for party_name in winning_parties:
            # Get party image
            party_image = None
            if party_name in global_parties:
                party_image = global_parties[party_name]["image"]

            # Get vote count
            vote_count = db.session.query(func.count(Vote.id)).filter(
                Vote.party == party_name).scalar()

            # Get party members
            members = PartyMember.query.filter_by(party_name=party_name).all()
            member_info = [{"name": m.name, "position": m.position} for m in members]

            winning_details.append({
                "name": party_name,
                "image": party_image,
                "votes": vote_count,
                "members": member_info
            })

        winner_details = {
            "parties": winning_details,
            "total_votes": db.session.query(func.count(Vote.id)).scalar()
        }

    # We'll handle displaying the winner directly on the vote page
    # instead of redirecting to a separate winner page
    response_data = {
        "winner": status.winner if status.winner else None,
        "winner_details": winner_details,
        "election_open": status.is_open
    }

    # Only add countdown data if a countdown is active
    if status.countdown_end and status.countdown_end > datetime.datetime.now():
        response_data["countdown"] = {
            "seconds_remaining": int((status.countdown_end - datetime.datetime.now()).total_seconds())
        }
    else:
        response_data["countdown"] = None

    return jsonify(response_data)


@app.route("/admin/cancel_winner", methods=["POST"])
@admin_required
def cancel_winner():
    status = get_election_status()
    status.winner = None
    # Also reopen voting automatically as requested
    status.is_open = True
    db.session.commit()
    
    # Force a reload of any live results pages
    with open('winner_cancelled.txt', 'w') as f:
        f.write(f"Winner declaration cancelled at {datetime.datetime.now()}")

    flash("Winner declaration has been cancelled and voting has been reopened", "success")
    return redirect(url_for('admin_dashboard'))


@app.route("/admin/change_password", methods=["POST"])
@admin_required
def change_admin_password():
    current_password = request.form.get("current_password")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")
    
    # Verify current password
    if current_password != "10032010":
        flash("Current password is incorrect", "error")
        return redirect(url_for('admin_dashboard'))
        
    # Verify passwords match
    if new_password != confirm_password:
        flash("New passwords do not match", "error")
        return redirect(url_for('admin_dashboard'))
        
    if len(new_password) < 8:
        flash("New password must be at least 8 characters long", "error")
        return redirect(url_for('admin_dashboard'))
    
    # Update the password in the login route
    with open('main.py', 'r') as file:
        content = file.read()
        
    new_content = content.replace(
        'if password == "10032010":', 
        f'if password == "{new_password}":')
        
    with open('main.py', 'w') as file:
        file.write(new_content)
        
    flash("Password changed successfully! Please login again.", "success")
    return redirect(url_for('admin_logout'))

@app.route("/admin/logout")
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route("/winner/<party>")
def winner(party):
    """Display the winner page with confetti"""
    # Get election status to verify winner
    status = get_election_status()
    if not status.winner:
        return redirect(url_for('index'))

    # Verify that this is a valid party
    if party not in global_parties:
        return redirect(url_for('index'))

    # Get current vote counts with a single efficient query
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()
    for party, count in party_counts:
        vote_counts[party] = count

    # Prepare data for the template
    display_data = {}
    for p in global_parties:
        display_data[p] = {
            "image": global_parties[p]["image"],
            "count": vote_counts.get(p, 0)
        }

    # Get party members for the winner
    party_members = PartyMember.query.filter_by(party_name=party).all()
    member_list = [{"name": m.name, "position": m.position} for m in party_members]

    return render_template("winner.html",
                           winner=party,
                           winner_image=global_parties[party]["image"],
                           votes=display_data,
                           party_members=member_list)


def generate_update_event():
    """Generate SSE event with current data"""
    status = get_election_status()
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()
    for party, count in party_counts:
        vote_counts[party] = count

    display_data = {}
    for party in global_parties:
        display_data[party] = {"count": vote_counts.get(party, 0)}

    countdown_info = None
    if status.countdown_end and status.countdown_end > datetime.datetime.now():
        countdown_info = {
            "end_time": status.countdown_end.strftime("%B %d, %Y %H:%M:%S"),
            "seconds_remaining": int((status.countdown_end - datetime.datetime.now()).total_seconds())
        }

    data = {
        "votes": display_data,
        "admin_message": status.message,
        "election_open": status.is_open,
        "winner": status.winner,
        "countdown": countdown_info,
        "total_votes": sum(vote_counts.values()),
        "total_voters": db.session.query(func.count(Voter.id)).scalar()
    }

    return f"data: {json.dumps(data)}\n\n"

@app.route("/stream")
def stream():
    """SSE endpoint was removed - return empty response for backward compatibility"""
    return jsonify({"message": "SSE has been disabled, use /get_vote_counts instead"})

@app.route("/get_vote_counts")
def get_vote_counts():
    """API endpoint for initial data load"""
    status = get_election_status()

    # Always reload parties from database to ensure we have the latest
    global global_parties
    db_parties = Party.query.all()
    if db_parties:
        # Update global_parties with latest from database
        global_parties = {}
        for party in db_parties:
            global_parties[party.name] = {
                "image": party.image,
                "votes": 0  # Will be updated with vote counts
            }

    # Get vote counts for active parties only
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()

    # Only count votes for parties that still exist
    total_valid_votes = 0
    for party, count in party_counts:
        if party in global_parties:
            vote_counts[party] = count
            total_valid_votes += count

    # Build display data for each party
    display_data = {}
    for party in global_parties:
        display_data[party] = {
            "count": vote_counts.get(party, 0),
            "image": global_parties[party]["image"]
        }

    countdown_info = None
    if status.countdown_end and status.countdown_end > datetime.datetime.now():
        countdown_info = {
            "end_time": status.countdown_end.strftime("%B %d, %Y %H:%M:%S"),
            "seconds_remaining": int((status.countdown_end - datetime.datetime.now()).total_seconds())
        }

    data = {
        "votes": display_data,
        "admin_message": status.message,
        "election_open": status.is_open,
        "winner": status.winner,
        "countdown": countdown_info,
        "total_votes": sum(vote_counts.values()),
        "total_voters": db.session.query(func.count(Voter.id)).scalar()
    }

    return jsonify(data)


# Global variable declarations 
# (Adding this here to avoid SyntaxError: name 'parties' is used prior to global declaration)
global_parties = {
    "Party 1": {
        "image": "images/party1.svg",
        "votes": 0
    },
    "Party 2": {
        "image": "images/party2.svg",
        "votes": 0
    }
}

# Route for site settings
@app.route("/admin/site_settings", methods=["GET", "POST"])
@admin_required
def site_settings():
    settings = get_site_settings()

    if request.method == "POST":
        # Update site settings
        settings.site_title = request.form.get("site_title", "").strip() or "MUSLIM BHADALA JAMAAT"
        settings.site_subtitle = request.form.get("site_subtitle", "").strip() or "CHAIRMAN ELECTIONS 2025"
        
        # Update theme color
        theme_color = request.form.get("theme_color")
        if theme_color and theme_color.startswith('#') and len(theme_color) == 7:
            settings.theme_color = theme_color

        # Check if a new logo was uploaded
        logo_file = request.files.get("logo_file")
        if logo_file and logo_file.filename:
            # Ensure directory exists
            if not os.path.exists('static/images'):
                os.makedirs('static/images')

            # Generate secure filename
            filename = secure_filename(logo_file.filename)
            ext = os.path.splitext(filename)[1]
            new_filename = f"main-logo-{uuid.uuid4().hex}{ext}"

            # Save the image
            image_path = os.path.join('static', 'images', new_filename)
            logo_file.save(image_path)

            # Update logo path in database
            settings.logo_path = f"images/{new_filename}"

        # Save changes
        db.session.commit()
        flash("Site settings updated successfully", "success")
        return redirect(url_for('admin_dashboard'))

    return render_template("site_settings.html", settings=settings)

# Route for managing parties
@app.route("/admin/manage_parties", methods=["GET", "POST"])
@admin_required
def manage_parties():
    # Access the global parties dictionary
    global global_parties

    # Ensure images directory exists
    if not os.path.exists('static/images'):
        os.makedirs('static/images')

    # Get current vote counts
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()
    for party, count in party_counts:
        vote_counts[party] = count

    # Load parties from database to ensure consistency
    db_parties = Party.query.all()
    if db_parties and not global_parties:
        global_parties = {}
        for party in db_parties:
            global_parties[party.name] = {
                "image": party.image,
                "votes": vote_counts.get(party.name, 0)
            }

    # Handle form submission
    if request.method == "POST":
        party_count = int(request.form.get("party_count", 2))
        new_parties = {}

        # Process each party's data
        for i in range(party_count):
            party_name = request.form.get(f"party_name_{i}", "").strip()

            if not party_name:
                continue  # Skip empty party names

            # Check if an image was uploaded
            party_image = request.files.get(f"party_image_{i}")

            if party_image and party_image.filename:
                # Generate a secure filename with uuid to avoid conflicts
                filename = secure_filename(party_image.filename)
                ext = os.path.splitext(filename)[1]
                new_filename = f"{uuid.uuid4().hex}{ext}"

                # Save the image
                image_path = os.path.join('static', 'images', new_filename)
                party_image.save(image_path)

                # Add to new parties dictionary
                new_parties[party_name] = {
                    "image": f"images/{new_filename}",
                    "votes": vote_counts.get(party_name, 0)
                }
            else:
                # If no image was uploaded, use a default image or existing one
                existing_image = None
                if party_name in global_parties:
                    existing_image = global_parties[party_name]["image"]

                new_parties[party_name] = {
                    "image": existing_image or "images/vote-icon.svg",
                    "votes": vote_counts.get(party_name, 0)
                }

        # Update the global parties dictionary
        global_parties = new_parties

        # Save to database via Party model
        # First, clear existing parties
        Party.query.delete()

        # Add new parties - ensure we have at least one party
        if len(global_parties) == 0:
            # Add default parties if none provided
            global_parties = {
                "Party 1": {
                    "image": "images/party1.svg",
                    "votes": 0
                },
                "Party 2": {
                    "image": "images/party2.svg",
                    "votes": 0
                }
            }

        # Add all parties to database
        for name, data in global_parties.items():
            party = Party(name=name, image=data["image"])
            db.session.add(party)

        db.session.commit()

        # Update any votes that reference old parties that may have been renamed
        vote_parties = db.session.query(Vote.party).distinct().all()
        for party_tuple in vote_parties:
            old_party = party_tuple[0]
            if old_party not in global_parties:
                # Keep the old party name since we can't delete votes
                Vote.query.filter_by(party=old_party).update({"party": old_party})
                Voter.query.filter_by(party=old_party).update({"party": old_party})

        db.session.commit()

        # Update the election status to indicate system update - without showing message
        status = get_election_status()
        status.message = None  # Clear any previous message
        db.session.commit()

        flash("Parties updated successfully", "success")

        # Instead of redirecting, return JSON response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True})
        else:
            return redirect(url_for('manage_parties'))

    # Get current party count for the form
    current_party_count = len(global_parties)

    return render_template("manage_parties.html", 
                           parties=global_parties, 
                           vote_counts=vote_counts,
                           current_party_count=current_party_count)

# API endpoints for party members
@app.route("/api/party_members")
@admin_required
def get_party_members():
    party_name = request.args.get("party_name", "")
    if not party_name:
        return jsonify({"success": False, "message": "Party name is required", "members": []})

    members = PartyMember.query.filter_by(party_name=party_name).all()
    member_list = [{"id": m.id, "name": m.name, "position": m.position} for m in members]

    return jsonify({"success": True, "members": member_list})

@app.route("/api/party_members/add", methods=["POST"])
@admin_required
def add_party_member():
    data = request.json

    if not data:
        return jsonify({"success": False, "message": "No data provided"})

    party_name = data.get("party_name", "").strip()
    name = data.get("name", "").strip()
    position = data.get("position", "").strip()

    if not party_name or not name or not position:
        return jsonify({"success": False, "message": "All fields are required"})

    # Check if the party exists
    party = Party.query.filter_by(name=party_name).first()
    if not party and party_name not in global_parties:
        return jsonify({"success": False, "message": "Party does not exist"})

    # Create a new party member
    member = PartyMember(party_name=party_name, name=name, position=position)
    db.session.add(member)

    try:
        db.session.commit()
        return jsonify({
            "success": True, 
            "member": {
                "id": member.id,
                "name": member.name,
                "position": member.position
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/party_members/remove", methods=["POST"])
@admin_required
def remove_party_member():
    data = request.json

    if not data:
        return jsonify({"success": False, "message": "No data provided"})

    member_id = data.get("member_id")

    if not member_id:
        return jsonify({"success": False, "message": "Member ID is required"})

    member = PartyMember.query.get(member_id)
    if not member:
        return jsonify({"success": False, "message": "Member not found"})

    try:
        db.session.delete(member)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)})

# Auto-refresh functionality for admin dashboard
@app.route("/api/admin/dashboard_data")
@admin_required
def admin_dashboard_data():
    """API endpoint for refreshing admin dashboard data"""
    # Always reload parties from database for fresh data
    global global_parties
    db_parties = Party.query.all()
    if db_parties:
        # Update global_parties with latest from database
        global_parties = {}
        for party in db_parties:
            global_parties[party.name] = {
                "image": party.image,
                "votes": 0  # Will be updated with vote counts
            }

    # Get vote counts for active parties only
    vote_counts = {}
    party_counts = db.session.query(Vote.party, func.count(Vote.id)).group_by(
        Vote.party).all()
    
    # Only count votes for parties that still exist
    total_valid_votes = 0
    for party, count in party_counts:
        if party in global_parties:
            vote_counts[party] = count
            total_valid_votes += count
        
    # Get total number of voters (this remains unchanged)
    total_voters = db.session.query(func.count(Voter.id)).scalar()
    
    # Use valid votes total instead of all votes
    total_votes = total_valid_votes
    vote_data = {}

    for party in global_parties:
        vote_count = vote_counts.get(party, 0)
        percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0
        vote_data[party] = {
            "count": vote_count,
            "percentage": round(percentage, 1)
        }

    # Get election status
    status = get_election_status()

    # Prepare response
    # Get all voters for the table
    all_voters = Voter.query.order_by(Voter.voted_at.desc()).all()
    voters_data = [{
        'voter_id': voter.voter_id,
        'name': voter.name,
        'party': voter.party,
        'voted_at': voter.voted_at.replace(tzinfo=pytz.UTC).astimezone(NAIROBI_TZ).strftime('%Y-%m-%d %H:%M:%S')
    } for voter in all_voters]

    response = {
        "vote_data": vote_data,
        "total_voters": total_voters,
        "total_votes": total_votes,
        "election_open": status.is_open,
        "countdown_active": bool(status.countdown_end and status.countdown_end > datetime.datetime.now()),
        "winner": status.winner,
        "all_voters": voters_data
    }

    if status.countdown_end and status.countdown_end > datetime.datetime.now():
        time_remaining = status.countdown_end - datetime.datetime.now()
        response["time_remaining"] = int(time_remaining.total_seconds())

    return jsonify(response)

# Create database tables and initial election status
with app.app_context():
    db.create_all()

    # Initialize election status if it doesn't exist
    if not ElectionStatus.query.first():
        status = ElectionStatus(is_open=True)
        db.session.add(status)
        db.session.commit()
        logging.info("Created initial election status")

    # Initialize default parties if they don't exist
    if not Party.query.first():
        for name, data in global_parties.items():
            party = Party(name=name, image=data["image"])
            db.session.add(party)
        db.session.commit()
        logging.info("Created initial parties")

    # Initialize site settings if they don't exist
    if not SiteSettings.query.first():
        site_settings = SiteSettings()
        db.session.add(site_settings)
        db.session.commit()
        logging.info("Created initial site settings")
        
    # Initialize music settings if they don't exist
    if not MusicSettings.query.first():
        music_settings = MusicSettings(enabled=False)
        db.session.add(music_settings)
        db.session.commit()
        logging.info("Created initial music settings")

# Route for uploading custom background music
@app.route("/upload-music", methods=["GET", "POST"])
@admin_required
def upload_music():
    site_settings = get_site_settings()
    message = None
    
    if request.method == "POST":
        music_file = request.files.get("music_file")
        
        if music_file and music_file.filename:
            # Ensure directory exists
            if not os.path.exists('static/sounds'):
                os.makedirs('static/sounds')
            
            # Generate a timestamp for cache busting
            timestamp = int(time.time())
            
            # Save the music file with timestamp (overwriting the existing one)
            # We'll keep the filename consistent but use query parameters in the HTML for cache busting
            music_path = os.path.join('static', 'sounds', 'background-music.mp3')
            music_file.save(music_path)
            
            # Add a file with the current timestamp to trigger browser cache refresh
            version_path = os.path.join('static', 'sounds', 'music-version.txt')
            with open(version_path, 'w') as f:
                f.write(str(timestamp))
                
            # Log the upload for debugging
            logging.info(f"New background music uploaded at {timestamp}")
            
            flash("Custom background music uploaded successfully", "success")
            return redirect(url_for('admin_dashboard'))
            
    # Get settings for the template
    settings = get_site_settings()
    return render_template("upload_music.html", 
                          settings=settings)

# Route to check if a voter ID has already voted
@app.route('/check_voter_id')
def check_voter_id():
    """API endpoint to check if a voter ID has already voted"""
    voter_id = request.args.get('voter_id', '')
    if not voter_id:
        return jsonify({'exists': False, 'error': 'No voter ID provided'})
    
    # Check if voter ID exists in database
    voter = Voter.query.filter_by(voter_id=voter_id).first()
    return jsonify({'exists': voter is not None})

# Routes for remote music control
@app.route('/check_music_status')
def check_music_status():
    """API endpoint for clients to check if music should be playing"""
    music_settings = get_music_settings()
    return jsonify({
        'should_play': music_settings.enabled,
        'timestamp': int(time.time())
    })

@app.route('/toggle_music', methods=['POST'])
@admin_required
def toggle_music():
    """API endpoint for admin to toggle music on/off"""
    try:
        data = request.json
        if data is None:
            return jsonify({'success': False, 'message': 'Invalid data format'})
        
        enable = data.get('enable', False)
        
        # Update music settings in database
        music_settings = get_music_settings()
        music_settings.enabled = enable
        music_settings.last_updated = datetime.datetime.now()
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Music ' + ('enabled' if enable else 'disabled')
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# Configure error handling
@app.errorhandler(500)
def handle_500_error(error):
    db.session.rollback()
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)