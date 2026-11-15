

def test_upload_name_gets_an_extension_from_the_mime_type():
    from semechka.adapters.spark_media import upload_name
    assert upload_name("Video 12 Nov", "video/mp4") == "Video 12 Nov.mp4"
    assert upload_name("Picture 3", "image/png") == "Picture 3.png"
    assert upload_name("clip.MOV", "video/quicktime") == "clip.MOV"          # a known extension is kept
    assert upload_name("holiday.v2", "video/mp4") == "holiday.v2.mp4"        # an unknown suffix is not an extension
    assert upload_name("", "image/jpeg") == "upload.jpg"
    assert upload_name("a/b", "video/webm") == "a_b.webm"
    assert upload_name("x", "application/octet-stream") == "x"
