from fastapi import APIRouter
from app.models.media_models import MediaConfigResponse, MediaConfigData, MediaProviderResponse, MediaCategoryResponse

router = APIRouter()

# Dummy Data matching the frontend implementation plan
# In the future, this data will be fetched from a database table
DUMMY_MEDIA_CONFIG = MediaConfigData(
    title="MEDIA",
    description="Choose a provider and category to start watching",
    pin="1234",
    providers=[
        MediaProviderResponse(
            id="premium_iptv_1",
            name="Premium IPTV",
            logo_url="https://example.com/images/premium_logo.png",
            is_active=True,
            categories=[
                MediaCategoryResponse(
                    id="WorldWide",
                    title="World TV",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/world.svg",
                    color_hex="#2196F3",
                    playlist_url="https://iptv-org.github.io/iptv/index.m3u",
                ),
                MediaCategoryResponse(
                    id="movies",
                    title="Movies",
                    type="vod",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/favicon%20(9).ico",
                    color_hex="#9C27B0",
                    playlist_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/movie.m3u",
                ),
                MediaCategoryResponse(
                    id="sports",
                    title="Sports",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/trophy.png",
                    color_hex="#4CAF50",
                    playlist_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/Sports2.m3u",
                ),
                MediaCategoryResponse(
                    id="BD",
                    title="Bangladesh",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/Flag_of_Bangladesh.svg.png",
                    color_hex="#4CAF50",
                    playlist_url="https://raw.githubusercontent.com/imShakil/tvlink/refs/heads/main/iptv.m3u8",
                ),
                MediaCategoryResponse(
                    id="usa",
                    title="USA",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/united-states-flag-icon.svg",
                    color_hex="#9C27B0",
                    playlist_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/usa.m3u",
                ),
                MediaCategoryResponse(
                    id="india",
                    title="India",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/Flag_of_India.svg",
                    color_hex="#9C27B0",
                    playlist_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/india.m3u",
                ),
            ],
        ),
        MediaProviderResponse(
            id="free_iptv_2",
            name="Free TV",
            logo_url="https://example.com/images/free_logo.png",
            is_active=True,
            categories=[
                MediaCategoryResponse(
                    id="SportzfySpecial",
                    title="Sportzfy Special",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/sportzyf-1.webp",
                    color_hex="#FF9800",
                    playlist_url="https://raw.githubusercontent.com/streamifytv/abbas/refs/heads/main/bd.m3u",
                ),
                MediaCategoryResponse(
                    id="SportzfySpecial2",
                    title="Sportzfy Special 2",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/sportzyf-1.webp",
                    color_hex="#FF9800",
                    playlist_url="https://piratestv.cdn-s.workers.dev/",
                ),
                MediaCategoryResponse(
                    id="Kids",
                    title="Kids 2.0",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/kids.webp",
                    color_hex="#FF9800",
                    playlist_url="https://codeberg.org/royjaalexa/Ccc/raw/commit/f466e97195c9b1af2536797efcc8773055cbcbde/kkz.txt",
                ),
                MediaCategoryResponse(
                    id="JagoBD",
                    title="JagoBD",
                    type="live",
                    logo_url="https://www.jagobd.com/wp-content/uploads/2015/10/web_hi_res_512.png",
                    color_hex="#FF9800",
                    playlist_url="https://m3u-tvb.pages.dev/Jjago.br.m3u8",
                ),
                MediaCategoryResponse(
                    id="Pakistan",
                    title="Pakistan",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/pak.webp",
                    color_hex="#FF9800",
                    playlist_url="https://playlists-by-playztv.pages.dev/c-pkk.m3u",
                ),
                MediaCategoryResponse(
                    id="DAZN",
                    title="DAZN",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/dazn-1.webp",
                    color_hex="#FF9800",
                    playlist_url="https://wasitv-pro.site/sportz.php?token=1wzIrANIxEtHDHKdVw9yYSddXRs_wyxn3PzVQN_mnIF4",
                ),

                MediaCategoryResponse(
                    id="CanaisdoBrasil",
                    title="Canais do Brasill",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/brasil.webp",
                    color_hex="#FF9800",
                    playlist_url="https://wasitv-pro.site/sportz.php?token=1wzIrANIxEtHDHKdVw9yYSddXRs_wyxn3PzVQN_mnIF4&gid=348832819",
                ),
                MediaCategoryResponse(
                    id="SunNXT",
                    title="Sun NXT",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/sun.webp",
                    color_hex="#FF9800",
                    playlist_url="https://playlists-by-playztv.pages.dev/snxt",
                ),

                MediaCategoryResponse(
                    id="AfricanSports",
                    title="African Sports",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/clipboard-image-1774685432.png",
                    color_hex="#FF9800",
                    playlist_url="https://wasitv-pro.site/sportz.php?token=1wzIrANIxEtHDHKdVw9yYSddXRs_wyxn3PzVQN_mnIF4&gid=369105542",
                ),
                MediaCategoryResponse(
                    id="ArabicSports",
                    title="Arabic Sports",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/arab-1.webp",
                    color_hex="#FF9800",
                    playlist_url="https://wasitv-pro.site/sportz.php?token=1wzIrANIxEtHDHKdVw9yYSddXRs_wyxn3PzVQN_mnIF4&gid=1185593386",
                ),
                MediaCategoryResponse(
                    id="Fancode-IND",
                    title="Fancode - IND",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/fc.webp",
                    color_hex="#FF9800",
                    playlist_url="https://raw.githubusercontent.com/doctor-8trange/zyphx8/refs/heads/main/data/fancode.m3u",
                ),
                MediaCategoryResponse(
                    id="Fancode-BD",
                    title="Fancode - BD",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/fc.webp",
                    color_hex="#FF9800",
                    playlist_url="https://raw.githubusercontent.com/doctor-8trange/zyphx8/refs/heads/main/data/bd.m3u",
                ),  
                MediaCategoryResponse(
                    id="YuppTV",
                    title="Yupp TV",
                    type="live",
                    logo_url="https://d229kpbsb5jevy.cloudfront.net/bott/v2/networks/circularimages/yupptv.png",
                    color_hex="#FF9800",
                    playlist_url="https://raw.githubusercontent.com/streamifytv/abbas/refs/heads/main/Yupp.m3u",
                ),
                MediaCategoryResponse(
                    id="Toffee(BDOnly)",
                    title="Toffee (BD Only)",
                    type="live",
                    logo_url="https://apkfolder.io/wp-content/uploads/2026/03/Toffee.webp",
                    color_hex="#FF9800",
                    playlist_url="https://raw.githubusercontent.com/BINOD-XD/Toffee-Auto-Update-Playlist/refs/heads/main/toffee_NS_Player.m3u",
                ),
            ],
        ),
        MediaProviderResponse(
            id="adult_18",
            name="Adult",
            logo_url="https://example.com/images/free_logo.png",
            is_active=True,
            categories=[
                MediaCategoryResponse(
                    id="adult_18",
                    title="Adult (18+)",
                    type="vod",
                    logo_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/18-plus-age-restriction-icon.svg",
                    color_hex="#F44336",
                    playlist_url="https://raw.githubusercontent.com/freelancermilonid132bd-ui/apphub/refs/heads/main/adult.m3u",
                    requires_pin=False,
                ),
            ],
        )
    ]
)

@router.get("/media/providers", response_model=MediaConfigResponse, tags=["Media Center"])
async def get_media_providers() -> MediaConfigResponse:
    """
    Get the configuration for the Media Page, including active IPTV providers and their categories.
    """
    return MediaConfigResponse(
        status="success",
        data=DUMMY_MEDIA_CONFIG
    )


