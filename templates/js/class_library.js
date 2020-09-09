$(function() {
	
	// On clicking a class tab, store the tab in memory
	$('#myTab a').on('click', function (e) {
		e.preventDefault();
		$(this).tab('show');
		var clickedTabId = $(e.target).prop ('id');
		localStorage.setItem('libraryTab', clickedTabId);
	  })
	
	// On load, search storage to find if we have stored a tab
	var savedLibraryTab = localStorage.getItem('libraryTab');
	$('#' + savedLibraryTab).tab('show');

	
	// Checker for library stats, updates once a second
	var libraryStats = function () {
		// On load, get the latest download number
		var fetchLibraryStats = function () {
			fetch('/api/library/stats').then(res => res.json()).then(data => {
				setLibraryStats(data.download_count);
			});
		};

		// Function to set library stats
		var setLibraryStats = function (downloadCount) {
			const libraryDownloadsElement = '.libraryDownloads';
			if (!($(libraryDownloadsElement).text () == downloadCount)) {
				$(libraryDownloadsElement).text (downloadCount);
				animateCSS (libraryDownloadsElement, 'heartBeat');
			}
		};

		// Fetch the latest file stats every second
		window.setInterval(function(){
			fetchLibraryStats ();
		}, 1000);
		
		const animateCSS = (element, animation, prefix = 'animate__') =>
			// We create a Promise and return it
			new Promise((resolve, reject) => {
				const animationName = `${prefix}${animation}`;
				const node = document.querySelector(element);

				node.classList.add(`${prefix}animated`, animationName);

				// When the animation ends, we clean the classes and resolve the Promise
				function handleAnimationEnd() {
					node.classList.remove(`${prefix}animated`, animationName);
					node.removeEventListener('animationend', handleAnimationEnd);

					resolve('Animation ended');
				}

				node.addEventListener('animationend', handleAnimationEnd);
			});
	};
	libraryStats();

	// Define global variable which is updated and accessed by handler function
	var libraryUploadId = 0;
	
	// Get the csrf token
	const csrftoken = Cookies.get('_csrf_token');

	// Handlers
	var showEditFormHandler = function(e) {
		// Get the library upload ID of the clicked item
		libraryUploadId = $(this).parent().closest('div[id]').attr('id');

		// Get library upload data via AJAX
		$.ajax({
			method: "GET",
			url: "/api/v1/library/" + libraryUploadId,
			headers: {'key': config.apiKey},
			error: function(jqXHR, textStatus, errorThrown) {
				alert('An error occured with the library API GET call.');
			},
			success: function(libraryUpload) {
				// Add the data to the edit form div
				$('#edited_upload_title').text(libraryUpload.title);
				$('#edited_upload_description').text(libraryUpload.description);

				var thumbnailFilename = libraryUpload.filename.split('.');
				$("#edited_upload_image").attr("src", "/static/thumbnails/" + thumbnailFilename[0] + ".jpeg");

				$('#formTitleField').val(libraryUpload.title);
				$('#formDescriptionField').val(libraryUpload.description);

			}
		});

		// Don't follow the href
		e.preventDefault();
	};

	var submitEditFormHandler = function(e) {
		// Send data via AJAX
		$.ajax({
			type: "PUT",
			url: "/api/v1/library/" + libraryUploadId,
			contentType: 'application/json',
			headers: {'key': config.apiKey, 'X-CSRFToken': csrftoken},
			data: JSON.stringify({
				title: $('#formTitleField').val(),
				description: $('#formDescriptionField').val()
			}),
			error: function(jqXHR, textStatus, errorThrown) {
				alert('An error occured with the library API PUT call.');
			},
			success: function(libraryUpload) {
				// Hide the modal div
				$('#editFormModal').modal('hide');

				//Update the original library card
				$('#' + libraryUploadId + " #libraryCardTitle").text(libraryUpload.title);
				$('#' + libraryUploadId + " #library_card-description").text(libraryUpload.description);

				toastr.success('Library item ' + libraryUpload.title + ' updated successfully.')

			}
		});

		// Don't POST the form
		e.preventDefault();
	};

	// Events
	$('.editLibraryUpload').click(showEditFormHandler);
	$('#editLibraryUploadForm').submit(submitEditFormHandler);
	$('#modalSaveButton').click(submitEditFormHandler);

});