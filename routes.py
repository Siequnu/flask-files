from flask import render_template, flash, redirect, url_for, request, current_app, send_file, abort, session, make_response, jsonify
from flask_login import current_user
from flask_login import login_required

import app.assignments.models
from app import db
from app.files import bp, models, forms
from app.models import Comment, Download, Upload, Turma, ClassLibraryFile, Enrollment, Assignment, LibraryUpload, LibraryDownload, User

import random, os, re, base64

# API route to get a filename from a file
@bp.route("/api/filename/<file_id>")
@login_required
def api_get_original_filename_from_id (file_id):
	if current_user.id == models.get_file_owner_id (file_id) or app.models.is_admin(current_user.username):
		return jsonify (app.files.models.get_original_filename_from_file_id (file_id))
	abort(403)

# Render this blueprint's javascript
@bp.route("/js")
@login_required
def js():
	response = make_response(render_template('js/class_library.js'))
	response.headers['Content-type'] = 'text/javascript'
	return response

# Access file stats
@bp.route("/uploads")
@login_required
def file_stats():
	if current_user.is_authenticated and app.models.is_admin(current_user.username):
		# Get total list of uploaded files from all users
		template_packages = {}
		template_packages['uploads_object'] = models.get_uploads_object()
		template_packages['total_upload_count'] = str(models.get_all_uploads_count())
		template_packages['total_assignments'] = Assignment.query.all(),
		template_packages['admin'] = True

		if current_user.is_superintendant is not True:
			filtered_list = []
			# Filter the list of files
			for upload, user, assignment in template_packages['uploads_object']:
				if app.classes.models.check_if_student_is_in_teachers_class (user.id, current_user.id):
					filtered_list.append ((upload, user, assignment))

			# Overwrite the original
			template_packages['uploads_object'] = filtered_list


		return render_template('files/file_stats_admin.html', template_packages = template_packages)
	elif current_user.is_authenticated:
		all_post_info = models.get_post_info_from_user_id (current_user.id)
		return render_template('files/file_stats.html',
							   comment = Comment,
							   all_post_info = all_post_info)
	abort(403)



# Choose a random file from uploads folder and send it out for download
@bp.route('/download_random_file/<assignment_id>', methods=['POST'])
@login_required
def download_random_file(assignment_id):
	# Check if user has any previous downloads with pending peer reviews
	pending_comment = Comment.get_pending_status_from_user_id_and_assignment_id (current_user.id, assignment_id)
	if pending_comment is not None: 
		# User has a pending assignment, send them the same file as before
		flash('You have a peer review that you have not yet completed. You have redownloaded the same file.')
		filename = Upload.query.get(pending_comment.file_id).filename
		return models.download_file(filename)
	
	# Make sure not to give the same file to the same peer reviewer twice
	#!# This logic only works if there is a maximum of two peer reviews per student
	#!# Consider expanding this to allow more peer reviews.
	completed_comment = Comment.query.filter(
		Comment.assignment_id==assignment_id).filter(
		Comment.user_id==current_user.id).first()
	
	if completed_comment is None:
		uploads_not_from_user = Upload.query.filter(
			Upload.user_id != current_user.id).filter(
			Upload.assignment_id == assignment_id).all()
	else:
		uploads_not_from_user = Upload.query.filter(
			Upload.user_id != current_user.id).filter(
			Upload.assignment_id == assignment_id).filter(
			Upload.id != completed_comment.file_id).all()
	
	if len(uploads_not_from_user) == 0:
		flash('There are no files currently available for download. Please check back later.')
		return redirect(url_for('assignments.view_assignments'))
	
	# Attempt to assign "random" choice so that students receive at least 1 peer review per work.
	# If this is truly random some students end up with 4 and others 0
	
	# Make a population for each amount of peer reviews; no peer reviews = highest weighting
	no_peer_reviews = []
	one_peer_review = []
	more_peer_reviews = []
	for file in uploads_not_from_user:
		peer_reviews = app.files.models.get_peer_reviews_from_upload_id(file.id)
		if len(peer_reviews) == 0:
			no_peer_reviews.append(file)
		elif len(peer_reviews) == 1:
			one_peer_review.append(file)
		else:
			more_peer_reviews.append(file)
	
	# If there are files with no peer reviews, prefer those
	if len(no_peer_reviews) > 0:
		file = random.choice (no_peer_reviews)
	# If all files have at least 2 reviews, randomly assign from files with >3
	elif len(no_peer_reviews) == 0 and len(one_peer_review) == 0:
		file = random.choice (more_peer_reviews)
	# All files have at least one peer review. Selecting from files that have 1 or more peer reviews.
	else:
		# Pick weighted random population of files from one_peer_reviews and more_peer_reviews
		random_population = random.choices(population = [one_peer_review, more_peer_reviews],
				   weights = [0.8, 0.2],
				   k = 1)
		# Pick a file from the randomly selected population
		file = random.choice(random_population[0])
	
	# Update comments table with pending commment
	upload_id = Upload.query.filter_by(filename=file.filename).first().id
	comment_pending = Comment(user_id = int(current_user.id), file_id = int(upload_id),
							  pending = True, assignment_id=assignment_id)
	db.session.add(comment_pending)
	db.session.commit()

	# Download the file
	#!# Rename the file to something more sensible than a UUID?
	#!# Do not use original filename for privacy reasons	
	return app.files.models.download_file(file.filename, rename = False)


# Delete a file
@bp.route("/delete/<upload_id>", methods=['GET', 'POST'])
@bp.route("/delete/<upload_id>/redirect/<redirect_url>", methods=['GET', 'POST'])
@login_required
def delete_file(upload_id, redirect_url = False):
	if current_user.is_authenticated and app.models.is_admin(current_user.username):
		
		file = Upload.query.get(upload_id)
		if file is None:
			abort (404)
		
		# Is the student who uploaded this file part of this teacher's class?
		if current_user.is_superintendant is not True:
			if app.classes.models.check_if_student_is_in_teachers_class (file.user_id, current_user.id) is not True:
				abort (403)

		form = app.user.forms.ConfirmationForm()
		if form.validate_on_submit():
			# Delete any comments for this upload ID
			app.assignments.models.delete_all_comments_from_upload_id(upload_id)
			
			# Delete the upload
			app.files.models.delete_upload(upload_id)
			
			flash ('The file, and any associated comments or comment uploads were successfully deleted.', 'success')
			if not redirect_url:
				redirect_url = url_for('files.file_stats')
			else: 
				base64_bytes = redirect_url.encode('ascii')
				message_bytes = base64.b64decode(base64_bytes)
				redirect_url = message_bytes.decode('ascii')
			return redirect(redirect_url)
		
		return render_template(
			'confirmation_form.html',
			title = 'Delete file?',
			confirmation_message = 'Are you sure you want to delete ' + file.original_filename + '?',
			form = form)
	abort (403)
		


# Download a file for peer review
@bp.route("/download_file/<assignment_id>")
@login_required
def download_file(assignment_id):
	if app.assignments.models.check_if_assignment_is_over (assignment_id) == True:
		return render_template('files/download_file.html', assignment_id = assignment_id)
	else:
		# If the assignment hasn't closed yet, flash message to wait until after deadline
		flash('The assignment is not over. Please wait until the deadline is over, then try again to download an assignment to review.')
		return redirect (url_for('assignments.view_assignments'))


# Download any file from ID
@bp.route("/download/<file_id>")
@bp.route("/download/<file_id>/<rename>")
@login_required
def download (file_id, rename = True):
	# Get the file
	file = Upload.query.get(file_id)
	if file is None:
		abort (404)

	# Get the assignment
	assignment = Assignment.query.get(file.assignment_id)	
	if assignment is None: 
		abort (404)

	# For students using the open peer review
	if current_user.is_admin == False and assignment.open_peer_review == True and app.classes.models.check_if_student_is_in_class (current_user.id, assignment.target_turma_id) is True:
		# Get the filename and send to the download model
		return models.download_file(file.filename, rename = False) # i.e. get the UUID random filename

	# For normal download functions
	# Only file owner and admin can use this (we check for teacher permissions later)
	if current_user.id == models.get_file_owner_id (file_id) or app.models.is_admin(current_user.username):
			
		# If we are admin (only admin and file_owner make it to this part),
		# check if this upload was made for an assignment that this teacher created
		if app.models.is_admin(current_user.username):
			
			if app.classes.models.check_if_turma_id_belongs_to_a_teacher (assignment.target_turma_id, current_user.id) is False:
				abort (403)

		# Send to the download model
		return models.download_file(file.filename, rename)

	else:
		abort (404)

# Student form to upload a file to an assignment.
# An admin can override this with a student number, to submit work for them.
@bp.route('/upload/<assignment_id>', methods=['GET', 'POST'])
@bp.route('/upload/<assignment_id>/<user_id>', methods=['GET', 'POST'])
@login_required
def upload_file(assignment_id, user_id = False):
	# If we are in "view as student mode", quit, as we don't want teachers posing as students messing up the assignment distribution algo
	if current_user.can_return_to_admin is True:
		flash ('You are currently in student view, and can not upload student assignments.', 'info')
		return (redirect (url_for ('main.index')))
	
	# Only admin can force submit for another student
	#¡# If not superintendant, needs to check if the student is in this teacher's class
	if user_id:
		if not current_user.is_authenticated and app.models.is_admin(current_user.username):
			abort (403)
			
	# If this assignment is over the deadline, and user is not admin, abort
	if not app.models.is_admin(current_user.username):
		if app.assignments.models.check_if_assignment_is_over(assignment_id):
			abort(403)
				
	# If the form has been filled out and posted:
	if request.method == 'POST':
		if 'file' not in request.files:
			flash('No file uploaded. Please try again or contact your tutor.', 'warning')
			return redirect(request.url)
		
		file = request.files['file']
		
		if re.findall(r'[\u4e00-\u9fff]+', file.filename) != []:
			# There are Chinese characters in the filename
			flash('Your filename contains Chinese characters. Please use only English letters and numbers in your filename.', 'warning')
			return redirect(request.url)
		
		if file.filename == '':
			flash('The filename is blank. Please rename the file.', 'warning')
			return redirect(request.url)
		
		if file and models.allowed_file_extension(file.filename):
			models.save_assignment_file(file, assignment_id, user_id)
			original_filename = models.get_secure_filename(file.filename)
			flash('Your file ' + str(original_filename) + ' was submitted successfully.', 'success')
			
			if current_user.is_admin:
				return redirect(url_for('assignments.view_assignment_details', assignment_id = assignment_id))
			else:
				return redirect(url_for('assignments.view_assignments'))
		
		else:
			flash('You can not upload this kind of file. Please use an iWork, Office or PDF document.', 'warning')
			return redirect(url_for('assignments.view_assignments'))
	else:
		return render_template('files/upload_file.html')


# Student or admin route to replace an uploaded file before a deadline
@bp.route('/upload/replace/<upload_id>', methods=['GET', 'POST'])
@login_required
def replace_uploaded_file(upload_id):
	if current_user.id == models.get_file_owner_id (upload_id) or app.models.is_admin(current_user.username):
		# If this assignment is over the deadline, and user is not admin, abort	
		try:
			upload = Upload.query.get(upload_id)
			assignment = Assignment.query.get(upload.assignment_id)
		except:
			flash ('There was an error retrieving the upload assignment information', 'error')
			abort (404)
		
		if not app.models.is_admin(current_user.username):
			if app.assignments.models.check_if_assignment_is_over(assignment.id):
				abort(403)
				
		# If the form has been filled out and posted:
		if request.method == 'POST':
			if 'file' not in request.files:
				flash('No file uploaded. Please try again or contact your tutor.', 'warning')
				return redirect(request.url)
			file = request.files['file']
			if re.findall(r'[\u4e00-\u9fff]+', file.filename) != []:
				# There are Chinese characters in the filename
				flash('Your filename contains Chinese characters. Please use only English letters and numbers in your filename.', 'warning')
				return redirect(request.url)
			if file.filename == '':
				flash('The filename is blank. Please rename the file.', 'warning')
				return redirect(request.url)
			if file and models.allowed_file_extension(file.filename):
				# Delete the original upload and any associated comments
				models.delete_upload (upload_id)
				
				# Save the new file
				models.save_assignment_file(file, assignment.id, current_user.id)
				original_filename = models.get_secure_filename(file.filename)
				flash('Your file ' + str(original_filename) + ' was submitted successfully.', 'success')
				return redirect(url_for('assignments.view_assignments'))
			else:
				flash('You can not upload this kind of file. Please use a iWork, Office or PDF document.', 'warning')
				return redirect(url_for('assignments.view_assignments'))
		else:
			# Get all assignment info with due dates and humanised deadlines
			assignment = app.assignments.models.get_user_assignment_info(user_id = current_user.id, assignment_id = assignment.id)
			return render_template('files/replace_uploaded_file.html', current_upload = upload, assignment = assignment)
	abort (403)

	

@bp.route("/comments/<file_id>", methods=['GET', 'POST'])
@login_required
def view_comments(file_id):
	if current_user.id == models.get_file_owner_id (file_id) or app.models.is_admin(current_user.username):
		upload = models.get_upload_object (file_id)
		comments = models.get_peer_reviews_from_upload_id (file_id)
		
		return render_template('files/view_comments.html', comments = comments, upload = upload)
	abort (403)
	
	

	
@bp.route("/library/")
@login_required
def class_library():
	if current_user.is_authenticated and app.models.is_admin(current_user.username):
		classes = app.classes.models.get_teacher_classes_from_teacher_id (current_user.id)
		library = app.files.models.get_all_library_books ()
		total_library_downloads = app.files.models.get_total_library_downloads_count ()
		student_count = app.user.models.get_total_user_count()
		form = forms.LibraryUploadForm()
		form.target_turmas.choices = [(turma.id, turma.turma_label) for turma in classes]
		return render_template(
			'files/class_library.html', 
			admin = True, 
			classes = classes, 
			library = library,
			total_library_downloads = total_library_downloads,
			student_count = student_count,
			form = form)
	else:
		library = app.files.models.get_user_library_books_from_id (current_user.id)
		enrollment = app.assignments.models.get_user_enrollment_from_id(current_user.id)
		return render_template('files/class_library.html', library = library, enrollment = enrollment)
	abort (403)
	

# Route to download a library file
@bp.route('/library/download/<library_upload_id>')
@login_required
def download_library_file(library_upload_id):
	# Check if the user is part of this file's class
	if app.models.is_admin(current_user.username):
		library_upload = LibraryUpload.query.get(library_upload_id)
		if library_upload_id is None: abort (404)

		# Check if this file belongs to the current teacher vieweing
		if app.files.models.check_if_library_file_belongs_to_teacher (library_upload.id, current_user.id) is False:
			abort (403)
		
		# All checks passed, download file
		return app.files.models.download_library_file (library_upload_id)
		
	elif db.session.query(ClassLibraryFile).join(
		Enrollment, ClassLibraryFile.turma_id == Enrollment.turma_id).filter(
		Enrollment.user_id == current_user.id).filter(
		ClassLibraryFile.library_upload_id == library_upload_id).first() is not None:
		return app.files.models.download_library_file (library_upload_id)
	abort (403)
	
	
# Route to download a library file
@bp.route('/library/view/downloads/<library_upload_id>')
@login_required
def view_library_downloads(library_upload_id):
	if current_user.is_authenticated and app.models.is_admin(current_user.username):
		library_upload = LibraryUpload.query.get(library_upload_id)

		# Check if this file belongs to the current teacher vieweing
		if app.files.models.check_if_library_file_belongs_to_teacher (library_upload.id, current_user.id) is False:
			abort (403)

		library_downloads = db.session.query(LibraryDownload, User).join(
			User, LibraryDownload.user_id == User.id).filter(
			LibraryDownload.library_upload_id==library_upload_id).all()
		return render_template('files/view_library_downloads.html',
								title='View library downloads',
								library_upload = library_upload,
								library_downloads = library_downloads)
	abort (403)

# Admin form to upload a library file
@login_required
@bp.route('/library/upload/', methods=['GET', 'POST'])
def upload_library_file():
	if app.models.is_admin(current_user.username):	
		form = forms.LibraryUploadForm()
		classes = app.classes.models.get_teacher_classes_from_teacher_id (current_user.id)
		form.target_turmas.choices = [(turma.id, turma.turma_label) for turma in classes]
		if form.validate_on_submit():
			file = form.library_upload_file.data
			
			if re.findall(r'[\u4e00-\u9fff]+', file.filename) != []:
				# There are Chinese characters in the filename
				flash('Your filename contains Chinese characters. Please use only English letters and numbers in your filename.', 'warning')
				return redirect(url_for('files.class_library'))
			
			if file.filename == '':
				flash('The filename is blank. Please rename the file.', 'warning')
				return redirect(url_for('files.class_library'))
			
			if file and models.allowed_file_extension(file.filename):
				description = form.description.data
				title = form.title.data
				target_turmas = form.target_turmas.data
				email_students = form.email_students
				
				app.files.models.new_library_upload (
					file, title, description, target_turmas, email_students)
				
				flash('New file successfully added to the library!', 'success')
				return redirect(url_for('files.class_library'))
			else:
				flash('You can not upload this kind of file. Please use an iWork, Office or PDF document.', 'warning')
		return render_template('files/upload_library_file.html', title='Upload library file', form=form)
	abort (403)
	

# Admin form to delete a library file
@bp.route('/library/delete/<library_upload_id>')
@bp.route('/library/delete/<library_upload_id>/<turma_id>')
@login_required
def delete_library_file(library_upload_id, turma_id = False):
	if app.models.is_admin(current_user.username):	
		
		library_upload = LibraryUpload.query.get(library_upload_id)
		if library_upload_id is None: abort (404)

		# Check if this file belongs to the current teacher vieweing
		if app.files.models.check_if_library_file_belongs_to_teacher (library_upload.id, current_user.id) is False:
			abort (403)

		app.files.models.delete_library_upload_from_id(library_upload_id, turma_id)
		flash('File deleted from the library!', 'success')
		return redirect(url_for('files.class_library'))
	abort (403)

	

# Admin form to edit a library file
@bp.route('/library/edit/<library_upload_id>', methods=['GET', 'POST'])
@login_required
def edit_library_file(library_upload_id):
	if app.models.is_admin(current_user.username):
		library_upload = LibraryUpload.query.get(library_upload_id)
		if library_upload_id is None: abort (404)

		# Check if this file belongs to the current teacher vieweing
		if app.files.models.check_if_library_file_belongs_to_teacher (library_upload.id, current_user.id) is False:
			abort (403)


		form = forms.EditLibraryUploadForm(obj=library_upload)
		if form.validate_on_submit():
			app.files.models.edit_library_upload(library_upload_id, form)
			flash('File edited successfully!', 'success')
			return redirect(url_for('files.class_library'))
		return render_template('files/edit_library_file.html', title='Edit library file', form=form)
	abort (403)
