
from flask import send_from_directory, current_app, url_for, render_template
from flask_login import current_user

from app import db
from app.models import User, Upload, Download, Assignment, Comment, CommentFileUpload, LibraryUpload, ClassLibraryFile, Enrollment, Turma, LibraryDownload

import app.files

from werkzeug import secure_filename

import os, uuid, arrow
from wand.image import Image
from dateutil import tz
from datetime import datetime

from app import executor
from threading import Thread

def check_if_library_file_belongs_to_teacher (library_upload_id, teacher_id):
    library_upload = LibraryUpload.query.get(library_upload_id)
    if library_upload is None: return False

    # Get list of classes this file is assigned to
    turmas = []
    for class_file_assignment in ClassLibraryFile.query.filter_by (library_upload_id = library_upload.id).all():
        turmas.append (str(class_file_assignment.turma_id))

    # Check if any of these turmas belong to the teacher in question
    for turma_id in turmas:
        if app.classes.models.check_if_turma_id_belongs_to_a_teacher (turma_id, teacher_id) is True:
            return True
    
    # No matching turmas found, return False
    return False

def new_library_files_since_last_seen ():
    try:
        last_book_upload_timestamp = db.session.query(Enrollment, User, ClassLibraryFile, LibraryUpload).join(
        User, Enrollment.user_id==User.id).join(
        ClassLibraryFile, Enrollment.turma_id==ClassLibraryFile.turma_id).join(
        LibraryUpload, ClassLibraryFile.library_upload_id==LibraryUpload.id).filter(
        Enrollment.user_id==current_user.id).order_by(LibraryUpload.timestamp.desc()).first().LibraryUpload.timestamp
        
        if User.query.get(current_user.id).last_seen < last_book_upload_timestamp: return True
        else: return False
    except:
        return False

# Send a bulk class email about the assignment
def send_async_assignment_notification_to_class(app, usernames_and_emails, class_id, title, url, app_name):
    with app.app_context():

        from app.email_model import send_email
        subject = f"{app_name} - new library upload"
        for user in usernames_and_emails:
            body = render_template(
                'email/new_library_upload.html',
                title = title,
                url = url,
                subject = subject,
                user = user,
                app_name = app_name
                )
            send_email(user['email'], subject, body)


def new_library_upload (file, title, description, target_turmas, email_students):
    """
    Add a new library file to the database
    """
    random_filename = app.files.models.save_file(file)
    original_filename = app.files.models.get_secure_filename(file.filename)
    library_upload = LibraryUpload (
        original_filename=original_filename,
        filename = random_filename,
        title = title,
        description = description,
        user_id = current_user.id)
    db.session.add(library_upload)
    db.session.flush() # Needed to access the library_upload.id in the next step
    
    # Add the file for each class
    for turma_id in target_turmas:
        new_class_library_file = ClassLibraryFile(library_upload_id = library_upload.id, turma_id = turma_id)
        db.session.add(new_class_library_file)
        db.session.commit()

        # Send async emails if wanted
        if email_students:
            users = app.classes.models.get_class_enrollment_from_class_id(turma_id)
            url = url_for('files.class_library', _external=True)
            usernames_and_emails = []
            title = title
            for enrollment, turma, user in users:
                usernames_and_emails.append ({
                    'username': user.username,
                    'email': user.email
                })
            app_name = current_app.config['APP_NAME']
            thr = Thread(
                target=send_async_assignment_notification_to_class, 
                args=[current_app._get_current_object(), usernames_and_emails, turma_id, title, url, app_name])
            thr.start()
        
    # Generate thumbnail
    executor.submit(get_thumbnail, library_upload.filename)


def edit_library_upload (library_upload_id, form):
    library_upload = LibraryUpload.query.get(library_upload_id)
    library_upload.title = form.title.data
    library_upload.description = form.description.data
                                
    db.session.commit()
    
# Delete upload and any comments
def delete_upload (upload_id):
    # Delete any grades from this file
    for grade in app.assignments.models.AssignmentGrade.query.filter_by(upload_id = upload_id):
        db.session.delete(grade)
    
    upload = Upload.query.get(upload_id)
    db.session.delete(upload)
    db.session.commit()

    
# Generate thumbnails
def get_thumbnail (filename):
    thumbnail_filename = filename.rsplit('.', 1)[0] + '.jpeg'
    thumbnail_filepath = (os.path.join(current_app.config['THUMBNAIL_FOLDER'], thumbnail_filename))
    if os.path.exists(thumbnail_filepath):
        return thumbnail_filepath
    else:
        file_extension = get_file_extension(filename)
        if file_extension == 'doc' or file_extension == 'docx' or file_extension == 'pages':
            filepath = (os.path.join(current_app.config['THUMBNAIL_FOLDER'], 'file-word.pdf'))
        elif file_extension == 'ppt' or file_extension == 'pptx' or file_extension == 'key':
            filepath = (os.path.join(current_app.config['THUMBNAIL_FOLDER'], 'file-powerpoint.pdf'))
        elif file_extension == 'jpeg' or file_extension == 'jpg' or file_extension == 'png':
            #filepath = (os.path.join(current_app.config['THUMBNAIL_FOLDER'], 'file-image.pdf')) # Why not make a thumnail out of the image?
            filepath = (os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        elif file_extension == 'zip' or file_extension == 'rar':
            filepath = (os.path.join(current_app.config['THUMBNAIL_FOLDER'], 'file-archive.pdf'))
        elif file_extension == 'pdf':
            filepath = (os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        else:
            filepath = (os.path.join(current_app.config['THUMBNAIL_FOLDER'], 'file-blank.pdf'))
        
        with(Image(filename=filepath, resolution=current_app.config['THUMBNAIL_RESOLUTION'])) as source: 
            images = source.sequence
            Image(images[0]).save(filename=thumbnail_filepath)
        return thumbnail_filepath

def get_all_library_books ():
    return db.session.query(ClassLibraryFile, LibraryUpload).join(
        LibraryUpload, ClassLibraryFile.library_upload_id==LibraryUpload.id).all(
    )

def delete_library_upload_from_id (library_upload_id, turma_id = False):
    if turma_id == False:
        # Remove the library upload from all classes
        if db.session.query(ClassLibraryFile).filter_by(library_upload_id=library_upload_id).all() is not None:
            class_library_files = db.session.query(ClassLibraryFile).filter_by(library_upload_id=library_upload_id).all()
            for library_file in class_library_files:
                ClassLibraryFile.query.filter_by(id=library_file.id).delete()
        
        # Remove any download records of the library upload
        if db.session.query(LibraryDownload).filter_by(library_upload_id=library_upload_id).all() is not None:
            library_downloads = db.session.query(LibraryDownload).filter_by(library_upload_id=library_upload_id).all()
            for library_download in library_downloads:
                db.session.delete(library_download)
        
        # Delete the Library Upload
        LibraryUpload.query.filter_by(id=library_upload_id).delete()
    else: # Only delete the class link.
        #!# If this is the last class link, delete the file itself?
        ClassLibraryFile.query.filter_by(turma_id=turma_id).filter_by(library_upload_id=library_upload_id).delete()
    db.session.commit()
    return
        

def get_user_library_books_from_id (user_id):
    return db.session.query(Enrollment, User, Turma, ClassLibraryFile, LibraryUpload).join(
        User, Enrollment.user_id==User.id).join(
        Turma, Enrollment.turma_id == Turma.id).join(
        ClassLibraryFile, Enrollment.turma_id==ClassLibraryFile.turma_id).join(
        LibraryUpload, ClassLibraryFile.library_upload_id==LibraryUpload.id).filter(
        Enrollment.user_id==user_id).all() 

def get_uploads_object ():
    return db.session.query(Upload, User, Assignment).join(
        User, Upload.user_id==User.id).join(
        Assignment, Upload.assignment_id==Assignment.id).all()
    
def get_all_uploads_count():
    return Upload.query.count()

def get_uploaded_file_count_from_user_id (user_id):
    return Upload.query.filter_by(user_id=current_user.id).count()

def get_file_owner_id (file_id):
    file = Upload.query.get (file_id)
    if file is not None:
        return Upload.query.get(file_id).user_id
    else:
        return 0 # No user can have user_id == 0, this starts at 1

def get_peer_reviews_from_upload_id (upload_id):
    comments = Comment.query.filter_by(file_id=upload_id).filter_by(pending=False).all()
    comments_array = []	
    for comment in comments:
        comment_dict = comment.__dict__
        user = User.query.get(comment.user_id)
        comment_dict['humanized_timestamp'] = arrow.get(comment.timestamp, tz.gettz('Asia/Hong_Kong')).humanize()
        comment_dict['comment_file_upload'] = db.session.query(CommentFileUpload).filter_by(comment_id = comment_dict['id']).first()
        comments_array.append((comment_dict, user))
    return comments_array

def get_upload_object (upload_id):
    return Upload.query.get(upload_id)

def get_post_info_from_user_id (user_id):   
    upload_info = db.session.query(Upload).filter(Upload.user_id==user_id).all()
    upload_array =[]
    for upload in upload_info:
        upload_dict = upload.__dict__ # Convert the SQL Alchemy object into dictionary
        upload_dict['number_of_comments'] = get_received_peer_review_from_upload_id_count(upload_dict['id'])
        upload_dict['comments'] = get_peer_reviews_from_upload_id (upload_dict['id'])
        upload_dict['humanized_timestamp'] = arrow.get(upload_dict['timestamp'], tz.gettz('Asia/Hong_Kong')).humanize()
        upload_array.append(upload_dict)
    return upload_array

def get_received_peer_review_from_upload_id_count (upload_id):
    return Comment.query.filter_by(file_id=upload_id).filter_by(pending=False).count()

def allowed_file_extension(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

def get_file_extension(filename):
    return filename.rsplit('.', 1)[1].lower()
    

def get_original_filename_from_file_id (file_id):
    file = Upload.query.get(file_id)
    if file is not None:
        return {'filename': file.original_filename}
    else:
        return {'error': 'Could not find this file.'}

def delete_uploads_enrollments_and_download_records_for_user (user_id):
    # Delete all upload records for this user
    assignment_uploads = Upload.query.filter_by(user_id=user_id).all()
    for upload in assignment_uploads:
        db.session.delete(upload)
        
    # Remove any enrollments of this user
    enrollments = Enrollment.query.filter_by(user_id=user_id).all()
    for enrollment in enrollments:
        db.session.delete(enrollment)
            
    library_downloads = LibraryDownload.query.filter_by(user_id=user_id).all()
    for library_download in library_downloads:
        db.session.delete(library_download)
            
    downloads = Download.query.filter_by(user_id=user_id).all()
    for download in downloads:
        db.session.delete(download)
        
    db.session.commit()


# Send out specific file for download
def download_file(filename, rename = False):
    # Log the download
    download = Download(filename = filename, user_id = current_user.id, timestamp = datetime.now())
    db.session.add(download)
    db.session.commit()
    
    # Send out the file
    if rename == False:
        return send_from_directory(
            directory = current_app.config['UPLOAD_FOLDER'],
            filename = filename, 
            as_attachment = True
        )
    elif rename == True:
        original_filename = Upload.query.filter_by(filename=filename).first().original_filename
        return send_from_directory(
            filename=filename, 
            directory=current_app.config['UPLOAD_FOLDER'],					   
            as_attachment = True, 
            attachment_filename = original_filename
        )

def get_total_library_downloads_count ():
    return len(LibraryDownload.query.all())

def download_library_file (library_upload_id):
    download = LibraryDownload(library_upload_id = library_upload_id, user_id = current_user.id, timestamp = datetime.now())
    db.session.add(download)
    db.session.commit()
    
    filename = LibraryUpload.query.get(library_upload_id).filename
    original_filename = LibraryUpload.query.get(library_upload_id).original_filename
    
    return send_from_directory(filename=filename, directory=current_app.config['UPLOAD_FOLDER'],
                                   as_attachment = True, attachment_filename = original_filename)


def get_total_downloads_for_user (user_id):
    return LibraryDownload.query.filter(LibraryDownload.user_id == user_id).count()
    

# Saves a file to uplaods folder, returns secure filename
def save_file (file):
    original_filename = secure_filename(file.filename)
    random_filename = get_random_uuid_filename (original_filename)
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], random_filename))
    return random_filename

# Save a file to uploads folder, and update DB
# Can also submit for another user, if user_id is supplied
def save_assignment_file (file, assignment_id, user_id = False):
    original_filename = secure_filename(file.filename)
    random_filename = save_file (file)
    
    executor.submit(get_thumbnail, random_filename)
    
    # Update SQL after file has saved
    new_upload = Upload(original_filename = original_filename, filename = random_filename, assignment_id = assignment_id, timestamp = datetime.now())
    
    if user_id:
        new_upload.user_id = user_id
    else:
        new_upload.user_id = current_user.id
        
    db.session.add(new_upload)
    db.session.commit()
    
    
# Save a comment file to be associated with a student upload
def save_comment_file_upload (file, comment_id):
    original_filename = secure_filename(file.filename)
    random_filename = save_file (file)
    
    executor.submit(get_thumbnail, random_filename)
    
    # Update DB after file has saved
    new_teacher_peer_review_file = CommentFileUpload(original_filename = original_filename,
                                                     filename = random_filename,
                                                     comment_id = comment_id,
                                                     user_id = current_user.id,
                                                     timestamp = datetime.now())
    db.session.add(new_teacher_peer_review_file)
    db.session.commit()

# Download a comment file 
def download_comment_file_upload (comment_file_upload_id):
    comment_file_upload = CommentFileUpload.query.get(comment_file_upload_id)
    
    return send_from_directory(filename=comment_file_upload.filename, directory=current_app.config['UPLOAD_FOLDER'],
                                   as_attachment = True, attachment_filename = comment_file_upload.original_filename)

# Verify a filename is secure with werkzeug library
def get_secure_filename(filename):
    return secure_filename(filename)

# Return randomised filename, keeping the original extension
def get_random_uuid_filename(original_filename):
    original_file_extension = get_file_extension(str(original_filename))
    random_filename = str(uuid.uuid4()) + '.' + original_file_extension
    return random_filename