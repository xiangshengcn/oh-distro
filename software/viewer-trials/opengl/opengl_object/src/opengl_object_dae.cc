#include <GL/gl.h>
#include <libxml/parser.h>
#include <libxml/tree.h>
#include <boost/algorithm/string.hpp>

#include "opengl/opengl_object_dae.h"

using namespace std;
using namespace KDL;
using namespace Eigen;
using namespace opengl;

OpenGL_Object_DAE::
OpenGL_Object_DAE( string id,
                    const Frame& transform,
                    const Frame& offset,
                    string filename ) : OpenGL_Object( id, transform, offset ),
                                        _v2_data(),
                                        _v3_data(),
                                        _v4_data(),
                                        _index_data(),
                                        _geometry_data(),
                                        _dae_offset( KDL::Frame::Identity() ),
                                        _dl( 0 ) {
  _load_opengl_object( filename );
}

OpenGL_Object_DAE::
~OpenGL_Object_DAE() 
{
  if( _dl != 0 && glIsList( _dl ) == GL_TRUE ){
    glDeleteLists( _dl, 1 );
    _dl = 0;
  }
  for( map< string, vector< Vector2f* > >::iterator it1 = _v2_data.begin(); it1 != _v2_data.end(); it1++ ){
    for ( vector< Vector2f* >::iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++){
      if( (*it2) != NULL ){
        delete (*it2 );
        (*it2) = NULL;
      }
    }
  }
  for( map< string, vector< Vector3f* > >::iterator it1 = _v3_data.begin(); it1 != _v3_data.end(); it1++ ){
    for ( vector< Vector3f* >::iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++){
      if( (*it2) != NULL ){
        delete (*it2 );
        (*it2) = NULL;
      }
    }
  }
  for( map< string, vector< Vector4f* > >::iterator it1 = _v4_data.begin(); it1 != _v4_data.end(); it1++ ){
    for ( vector< Vector4f* >::iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++){
      if( (*it2) != NULL ){
        delete (*it2 );
        (*it2) = NULL;
      }
    }
  }
}

OpenGL_Object_DAE::
OpenGL_Object_DAE( const OpenGL_Object_DAE& other ) : OpenGL_Object( other ),
                                                      _v2_data( other._v2_data ),
                                                      _v3_data( other._v3_data ),
                                                      _v4_data( other._v4_data ),
                                                      _index_data( other._index_data ),
                                                      _geometry_data( other._geometry_data ),
                                                      _dl( 0 ){

}

OpenGL_Object_DAE&
OpenGL_Object_DAE::
operator=( const OpenGL_Object_DAE& other ) {
  _id = other._id;
  _visible = other._visible;
  _color = other._color;
  _transparency = other._transparency;
  _transform = other._transform;
  _offset = other._offset;
  _v2_data = other._v2_data;
  _v3_data = other._v3_data;
  _v4_data = other._v4_data;
  _index_data = other._index_data;
  _geometry_data = other._geometry_data;
  _dl = 0;
  return (*this);
}

void
OpenGL_Object_DAE::
apply_transform( void ){    
  Frame origin = _transform * _offset * _dae_offset;

  GLdouble m[] = { origin( 0, 0 ), origin( 1, 0 ), origin( 2, 0 ), origin( 3, 0 ),
                        origin( 0, 1 ), origin( 1, 1 ), origin( 2, 1 ), origin( 3, 1 ),
                        origin( 0, 2 ), origin( 1, 2 ), origin( 2, 2 ), origin( 3, 2 ),
                        origin( 0, 3 ), origin( 1, 3 ), origin( 2, 3 ), origin( 3, 3 ) };
  glMultMatrixd( m );
  return;
}

void 
OpenGL_Object_DAE::
set_color( Vector3f color ){
  _color = color;
  for( map< string, map< string, string > >::const_iterator it1 = _geometry_data.begin(); it1 != _geometry_data.end(); it1++ ){
    vector< Vector4f* > * color_data = NULL;
    vector< unsigned int > * index_data = NULL;
    for( map< string, string >::const_iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++ ){
      if ( it2->first == "COLOR" ){
        map< string, vector< Vector4f* > >::iterator color_iterator = _v4_data.find( it2->second );
        if( color_iterator != _v4_data.end() ){
          color_data = &color_iterator->second;
        }
      }
    }
    map< string, vector< unsigned int > >::iterator index_iterator = _index_data.find( it1->first );
    if( index_iterator != _index_data.end() ){
      index_data = &index_iterator->second;
    }
    if( ( color_data != NULL ) && ( index_data != NULL ) ){
      for( unsigned int i = 0; i < ( index_data->size() / 4 ); i++ ){
        unsigned int color_index = (*index_data)[ 4 * i + 3 ];
        (*(*color_data)[ color_index ])( 0 ) = color( 0 );
        (*(*color_data)[ color_index ])( 1 ) = color( 1 );
        (*(*color_data)[ color_index ])( 2 ) = color( 2 );
      }
    }
  }
  if( glIsList( _dl ) == GL_TRUE ){
    glDeleteLists( _dl, 1 );
    _dl = 0;
  }
  return;
}

void 
OpenGL_Object_DAE::
set_color( Vector4f color ){
  Vector3f rgb( color( 0 ), color( 1 ), color( 2 ) );
  set_color( rgb );
  set_transparency( color( 3 ) );
  return;
}

void
OpenGL_Object_DAE::
set( Frame transform ){
  _transform = transform;
  return;
}

void
OpenGL_Object_DAE::
draw( void ){
  if( visible() ){
    if( glIsList( _dl ) == GL_TRUE ){
      glPushMatrix();
      apply_transform();
      glCallList( _dl );
      glPopMatrix();
    } else if ( _generate_dl() ){
      draw();
    }
  }
  return;
}

void
OpenGL_Object_DAE::
draw( Vector3f color ){
  if( visible() ){
    glPushMatrix();
    apply_transform();
    for( map< string, map< string, string > >::const_iterator it1 = _geometry_data.begin(); it1 != _geometry_data.end(); it1++ ){
      vector< Vector3f* > * vertex_data = NULL;
      vector< Vector3f* > * normal_data = NULL;
      vector< Vector2f* > * texcoord_data = NULL;
      vector< Vector4f* > * color_data = NULL;
      vector< unsigned int > * index_data = NULL;
      for( map< string, string >::const_iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++ ){
        if( it2->first == "NORMAL" ){
          map< string, vector< Vector3f* > >::iterator normal_iterator = _v3_data.find( it2->second );
          if( normal_iterator != _v3_data.end() ){
            normal_data = &normal_iterator->second;
          }
        } else if ( it2->first == "VERTEX" ){
          string vertex_name = "N/A";
          map< string, string >::const_iterator vertex_name_iterator = it1->second.find( it2->second );
          if( vertex_name_iterator != it1->second.end() ){
            vertex_name = vertex_name_iterator->second;
            map< string, vector< Vector3f* > >::iterator vertex_iterator = _v3_data.find( vertex_name );
            if( vertex_iterator != _v3_data.end() ){
              vertex_data = &vertex_iterator->second;
            }
          }
        }  else if ( it2->first == "TEXCOORD" ){
          map< string, vector< Vector2f* > >::iterator texcoord_iterator = _v2_data.find( it2->second );
          if( texcoord_iterator != _v2_data.end() ){
            texcoord_data = &texcoord_iterator->second;
          }
        } else if ( it2->first == "COLOR" ){
          map< string, vector< Vector4f* > >::iterator color_iterator = _v4_data.find( it2->second );
          if( color_iterator != _v4_data.end() ){
            color_data = &color_iterator->second;
          }
        }
      }
      map< string, vector< unsigned int > >::iterator index_iterator = _index_data.find( it1->first );
      if( index_iterator != _index_data.end() ){
        index_data = &index_iterator->second;
      }

      if( ( vertex_data != NULL ) && ( normal_data != NULL ) && ( index_data != NULL ) ){
        glBegin( GL_TRIANGLES );
        for( unsigned int i = 0; i < ( index_data->size() / 4 ); i++ ){
          unsigned int vertex_index = (*index_data)[ 4 * i + 0 ];
          unsigned int normal_index = (*index_data)[ 4 * i + 1 ];
          unsigned int color_index = (*index_data)[ 4 * i + 3 ];
          glColor4f( color( 0 ),
                      color( 1 ),
                      color( 2 ),
                      transparency() );
          glNormal3f( (*(*normal_data)[ normal_index ])( 0 ),
                      (*(*normal_data)[ normal_index ])( 1 ),
                      (*(*normal_data)[ normal_index ])( 2 ) );
          glVertex3f( (*(*vertex_data)[ vertex_index ])( 0 ),
                      (*(*vertex_data)[ vertex_index ])( 1 ),
                      (*(*vertex_data)[ vertex_index ])( 2 ) );
        }
        glEnd();
      } else if( ( vertex_data != NULL ) && ( normal_data != NULL ) && ( texcoord_data != NULL ) && ( index_data != NULL ) ){
        glBegin( GL_TRIANGLES );
        for( unsigned int i = 0; i < ( index_data->size() / 3 ); i++ ){
          unsigned int vertex_index = (*index_data)[ 3 * i + 0 ];
          unsigned int normal_index = (*index_data)[ 3 * i + 1 ];
          glColor4f( color( 0 ), color( 1 ), color( 2 ), transparency() );
          glNormal3f( (*(*normal_data)[ normal_index ])( 0 ),
                      (*(*normal_data)[ normal_index ])( 1 ),
                      (*(*normal_data)[ normal_index ])( 2 ) );
          glVertex3f( (*(*vertex_data)[ vertex_index ])( 0 ),
                      (*(*vertex_data)[ vertex_index ])( 1 ),
                      (*(*vertex_data)[ vertex_index ])( 2 ) );
        }
        glEnd();
      } else if( ( vertex_data != NULL ) && ( normal_data != NULL ) && ( index_data != NULL ) ){
        glBegin( GL_TRIANGLES );
        for( unsigned int i = 0; i < ( index_data->size() / 2 ); i++ ){
          unsigned int vertex_index = (*index_data)[ 2 * i + 0 ];
          unsigned int normal_index = (*index_data)[ 2 * i + 1 ];
          glColor4f( color( 0 ), color( 1 ), color( 2 ), transparency() );
          glNormal3f( (*(*normal_data)[ normal_index ])( 0 ),
                      (*(*normal_data)[ normal_index ])( 1 ),
                      (*(*normal_data)[ normal_index ])( 2 ) );
          glVertex3f( (*(*vertex_data)[ vertex_index ])( 0 ),
                      (*(*vertex_data)[ vertex_index ])( 1 ),
                      (*(*vertex_data)[ vertex_index ])( 2 ) );
        }
        glEnd();
      }
    }
    glPopMatrix();
  }

  return;
}
  
bool
OpenGL_Object_DAE::
_generate_dl( void ){
  if( glIsList( _dl ) == GL_TRUE ){
    glDeleteLists( _dl, 1 );
    _dl = 0;
  }
  _dl = glGenLists( 1 );
  glNewList( _dl, GL_COMPILE );
  for( map< string, map< string, string > >::const_iterator it1 = _geometry_data.begin(); it1 != _geometry_data.end(); it1++ ){
    vector< Vector3f* > * vertex_data = NULL;
    vector< Vector3f* > * normal_data = NULL;
    vector< Vector2f* > * texcoord_data = NULL;
    vector< Vector4f* > * color_data = NULL;
    vector< unsigned int > * index_data = NULL;
    for( map< string, string >::const_iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++ ){
      if( it2->first == "NORMAL" ){
        map< string, vector< Vector3f* > >::iterator normal_iterator = _v3_data.find( it2->second );
        if( normal_iterator != _v3_data.end() ){
          normal_data = &normal_iterator->second;
        }
      } else if ( it2->first == "VERTEX" ){
        string vertex_name = "N/A";
        map< string, string >::const_iterator vertex_name_iterator = it1->second.find( it2->second );
        if( vertex_name_iterator != it1->second.end() ){
          vertex_name = vertex_name_iterator->second;
          map< string, vector< Vector3f* > >::iterator vertex_iterator = _v3_data.find( vertex_name );
          if( vertex_iterator != _v3_data.end() ){
            vertex_data = &vertex_iterator->second;
          }
        }
      } else if ( it2->first == "TEXCOORD" ){
        map< string, vector< Vector2f* > >::iterator texcoord_iterator = _v2_data.find( it2->second );
        if( texcoord_iterator != _v2_data.end() ){
          texcoord_data = &texcoord_iterator->second;
        }
      } else if ( it2->first == "COLOR" ){
        map< string, vector< Vector4f* > >::iterator color_iterator = _v4_data.find( it2->second );
        if( color_iterator != _v4_data.end() ){
          color_data = &color_iterator->second;
        }
      }
    }
    map< string, vector< unsigned int > >::iterator index_iterator = _index_data.find( it1->first );
    if( index_iterator != _index_data.end() ){
      index_data = &index_iterator->second;
    }

    if( ( vertex_data != NULL ) && ( normal_data != NULL ) && ( texcoord_data != NULL ) && ( color_data != NULL ) && ( index_data != NULL ) ){
      glBegin( GL_TRIANGLES );
      for( unsigned int i = 0; i < ( index_data->size() / 4 ); i++ ){
        unsigned int vertex_index = (*index_data)[ 4 * i + 0 ];
        unsigned int normal_index = (*index_data)[ 4 * i + 1 ];
        unsigned int color_index = (*index_data)[ 4 * i + 3 ];
        glColor4f( (*(*color_data)[ color_index ])( 0 ),
                    (*(*color_data)[ color_index ])( 1 ),
                    (*(*color_data)[ color_index ])( 2 ),
                    (*(*color_data)[ color_index ])( 3 ) * transparency() );
        glNormal3f( (*(*normal_data)[ normal_index ])( 0 ),
                    (*(*normal_data)[ normal_index ])( 1 ),
                    (*(*normal_data)[ normal_index ])( 2 ) );
        glVertex3f( (*(*vertex_data)[ vertex_index ])( 0 ),
                    (*(*vertex_data)[ vertex_index ])( 1 ),
                    (*(*vertex_data)[ vertex_index ])( 2 ) );
      }
      glEnd();
    } else if( ( vertex_data != NULL ) && ( normal_data != NULL ) && ( texcoord_data != NULL ) && ( index_data != NULL ) ){
      glBegin( GL_TRIANGLES );
      for( unsigned int i = 0; i < ( index_data->size() / 3 ); i++ ){
        unsigned int vertex_index = (*index_data)[ 3 * i + 0 ];
        unsigned int normal_index = (*index_data)[ 3 * i + 1 ];
        glColor4f( _color( 0 ), _color( 1 ), _color( 2 ), transparency() );
        glNormal3f( (*(*normal_data)[ normal_index ])( 0 ),
                    (*(*normal_data)[ normal_index ])( 1 ),
                    (*(*normal_data)[ normal_index ])( 2 ) );
        glVertex3f( (*(*vertex_data)[ vertex_index ])( 0 ),
                    (*(*vertex_data)[ vertex_index ])( 1 ),
                    (*(*vertex_data)[ vertex_index ])( 2 ) );
      }
      glEnd();
    } else if( ( vertex_data != NULL ) && ( normal_data != NULL ) && ( index_data != NULL ) ){
      glBegin( GL_TRIANGLES );
      for( unsigned int i = 0; i < ( index_data->size() / 2 ); i++ ){
        unsigned int vertex_index = (*index_data)[ 2 * i + 0 ];
        unsigned int normal_index = (*index_data)[ 2 * i + 1 ];
        glColor4f( _color( 0 ), _color( 1 ), _color( 2 ), transparency() );
        glNormal3f( (*(*normal_data)[ normal_index ])( 0 ),
                    (*(*normal_data)[ normal_index ])( 1 ),
                    (*(*normal_data)[ normal_index ])( 2 ) );
        glVertex3f( (*(*vertex_data)[ vertex_index ])( 0 ),
                    (*(*vertex_data)[ vertex_index ])( 1 ),
                    (*(*vertex_data)[ vertex_index ])( 2 ) );
      }
      glEnd();
    } 
  }
  glEndList();
  return true;
}

void
OpenGL_Object_DAE::
_load_opengl_object( string filename ){
  xmlDocPtr doc = NULL;
  xmlNodePtr root = NULL;
  doc = xmlReadFile( filename.c_str(), NULL, 0 );
  if( doc != NULL ){
    root = xmlDocGetRootElement( doc );
    xmlNodePtr l1 = NULL;
    for( l1 = root->children; l1; l1 = l1->next ){
      if( l1->type == XML_ELEMENT_NODE ){
        if( xmlStrcmp( l1->name, ( const xmlChar * )( "library_geometries" ) ) == 0 ){
          xmlNodePtr l2 = NULL;
          for( l2 = l1->children; l2; l2 = l2->next ){
            if( l2->type == XML_ELEMENT_NODE ){
              if( xmlStrcmp( l2->name, ( const xmlChar * )( "geometry" ) ) == 0 ){
                string geometry_id = "N/A";
                xmlChar* l2_prop = xmlGetProp( l2, ( const xmlChar * )( "id" ) );
                if( l2_prop != NULL ){
                  geometry_id = ( char* )( l2_prop );
                  xmlFree( l2_prop );
                }
                xmlNodePtr l3 = NULL;
                for( l3 = l2->children; l3; l3 = l3->next ){
                  if( l3->type == XML_ELEMENT_NODE ){
                    if( xmlStrcmp( l3->name, ( const xmlChar * )( "mesh" ) ) == 0 ){
                      map< string, string > triangles_map;
                      xmlNodePtr l4 = NULL;
                      for( l4 = l3->children; l4; l4 = l4->next ){
                        if( l4->type == XML_ELEMENT_NODE ){
                          if( xmlStrcmp( l4->name, ( const xmlChar * )( "source" ) ) == 0 ){
                            string source_id = "N/A";
                            string float_array_id = "N/A";
                            unsigned int float_array_count = 0;
                            vector< string > float_array_content_vector;
                            unsigned int accessor_count = 0;
                            unsigned int accessor_stride = 0;
                            vector< Vector3f > vertices;
                            xmlChar* l4_prop = xmlGetProp( l4, ( const xmlChar * )( "id" ) );
                            if( l4_prop != NULL ){
                              source_id = ( char* )( l4_prop );
                              xmlFree( l4_prop );
                            }                       
                            xmlNodePtr l5 = NULL;
                            for( l5 = l4->children; l5; l5 = l5->next ){
                              if( l5->type == XML_ELEMENT_NODE ){
                                if( xmlStrcmp( l5->name, ( const xmlChar * )( "float_array" ) ) == 0 ){
                                  xmlChar* l5_prop = xmlGetProp( l5, ( const xmlChar * )( "id" ) );
                                  if( l5_prop != NULL ){
                                    float_array_id = ( char* )( l5_prop );
                                    xmlFree( l5_prop );
                                  }         
                                  l5_prop = xmlGetProp( l5, ( const xmlChar * )( "count" ) );
                                  if( l5_prop != NULL ){
                                    float_array_count = atoi( ( char * )( l5_prop ) );
                                    xmlFree( l5_prop );
                                  }
                                  xmlChar* l5_content = xmlNodeGetContent( l5 );
                                  string l5_content_string = ( char* )( l5_content );
                                  xmlFree( l5_content );
                                  boost::split( float_array_content_vector, l5_content_string, boost::is_any_of("\n "));
                                  for( unsigned int i = 0; i < float_array_content_vector.size(); i++ ){
                                    if( float_array_content_vector[ i ] == "" ){
                                      float_array_content_vector.erase( float_array_content_vector.begin() + i );
                                      i--;
                                    }
                                  }
                                } else if( xmlStrcmp( l5->name, ( const xmlChar * )( "technique_common" ) ) == 0 ){
                                  xmlNodePtr l6 = NULL;
                                  for( l6 = l5->children; l6; l6 = l6->next ){
                                    if( l6->type == XML_ELEMENT_NODE ){
                                      if( xmlStrcmp( l6->name, ( const xmlChar * )( "accessor" ) ) == 0 ){
                                        xmlChar* l6_prop = xmlGetProp( l6, ( const xmlChar * )( "stride" ) );
                                        if( l6_prop != NULL ){
                                          accessor_stride = atoi( ( char * )( l6_prop ) );
                                          xmlFree( l6_prop );
                                        }
                                        l6_prop = xmlGetProp( l6, ( const xmlChar * )( "count" ) );
                                        if( l6_prop != NULL ){
                                          accessor_count = atoi( ( char * )( l6_prop ) );
                                          xmlFree( l6_prop );
                                        } 
                                      }
                                    }
                                  } // L6
                                }
                              }
                            } // L5
                            if( accessor_stride == 2 ){
                              vector< Vector2f* > vertex_vector;
                              for( unsigned int i = 0; i < accessor_count; i++ ){
                                Vector2f * vertex = new Vector2f( strtof( float_array_content_vector[ 2 * i + 0 ].c_str(), NULL ),
                                                  strtof( float_array_content_vector[ 2 * i + 1 ].c_str(), NULL ) );
                                vertex_vector.push_back( vertex );
                              }
                              _v2_data.insert( make_pair( "#" + source_id, vertex_vector ) );
                            } else if( accessor_stride == 3 ){
                              vector< Vector3f* > vertex_vector;
                              for( unsigned int i = 0; i < accessor_count; i++ ){
                                Vector3f * vertex = new Vector3f( strtof( float_array_content_vector[ 3 * i + 0 ].c_str(), NULL ),
                                                  strtof( float_array_content_vector[ 3 * i + 1 ].c_str(), NULL ),
                                                  strtof( float_array_content_vector[ 3 * i + 2 ].c_str(), NULL ) );
                                vertex_vector.push_back( vertex );
                              }
                              _v3_data.insert( make_pair( "#" + source_id, vertex_vector ) );
                            } else if ( accessor_stride == 4 ){
                              vector< Vector4f* > vertex_vector;
                              for( unsigned int i = 0; i < accessor_count; i++ ){
                                Vector4f * vertex = new Vector4f( strtof( float_array_content_vector[ 4 * i + 0 ].c_str(), NULL ),
                                                  strtof( float_array_content_vector[ 4 * i + 1 ].c_str(), NULL ),
                                                  strtof( float_array_content_vector[ 4 * i + 2 ].c_str(), NULL ),
                                                  strtof( float_array_content_vector[ 4 * i + 3 ].c_str(), NULL ) );
                                vertex_vector.push_back( vertex );
                              }
                              _v4_data.insert( make_pair( "#" + source_id, vertex_vector ) );
                            }
                          } else if ( xmlStrcmp( l4->name, ( const xmlChar * )( "vertices" ) ) == 0 ){
                            string vertices_id = "N/A";
                            xmlChar* l4_prop = xmlGetProp( l4, ( const xmlChar * )( "id" ) );
                            if( l4_prop != NULL ){
                              vertices_id = ( char* )( l4_prop );
                              xmlFree( l4_prop );
                            }
                            xmlNodePtr l5 = NULL;
                            for( l5 = l4->children; l5; l5 = l5->next ){
                              if( l5->type == XML_ELEMENT_NODE ){
                                if( xmlStrcmp( l5->name, ( const xmlChar * )( "input" ) ) == 0 ){
                                  string source = "N/A";
                                  xmlChar* l5_prop = xmlGetProp( l5, ( const xmlChar * )( "source" ) );
                                  if( l5_prop != NULL ){
                                    source = ( char* )( l5_prop );
                                    xmlFree( l5_prop );
                                  }
                                  triangles_map.insert( make_pair( "#" + vertices_id, source ) ); 
                                } 
                              }
                            } // L5
                          } else if ( xmlStrcmp( l4->name, ( const xmlChar * )( "triangles" ) ) == 0 ){
                            xmlNodePtr l5 = NULL;
                            for( l5 = l4->children; l5; l5 = l5->next ){
                              if( l5->type == XML_ELEMENT_NODE ){
                                if( xmlStrcmp( l5->name, ( const xmlChar * )( "input" ) ) == 0 ){
                                  string semantic = "N/A";
                                  string source = "N/A";
                                  xmlChar* l5_prop = xmlGetProp( l5, ( const xmlChar * )( "semantic" ) );
                                  if( l5_prop != NULL ){
                                    semantic = ( char* )( l5_prop );
                                    xmlFree( l5_prop );
                                  }
                                  l5_prop = xmlGetProp( l5, ( const xmlChar * )( "source" ) );
                                  if( l5_prop != NULL ){
                                    source = ( char* )( l5_prop );
                                    xmlFree( l5_prop );
                                  }
                                  triangles_map.insert( make_pair( semantic, source ) );
                                } else if( xmlStrcmp( l5->name, ( const xmlChar * )( "p" ) ) == 0 ){
                                  vector< string > int_array_content_vector;
                                  xmlChar* l5_content = xmlNodeGetContent( l5 ); 
                                  string l5_content_string = ( char* )( l5_content );
                                  xmlFree( l5_content );
                                  boost::split( int_array_content_vector, l5_content_string, boost::is_any_of(" ") );
                                  for( unsigned int i = 0; i < int_array_content_vector.size(); i++ ){
                                    if( int_array_content_vector[ i ] == "" ){
                                      int_array_content_vector.erase( int_array_content_vector.begin() + i );
                                      i--;
                                    }
                                  }
                                  vector< unsigned int > index_data;
                                  for( unsigned int i = 0; i < int_array_content_vector.size(); i++ ){
                                    index_data.push_back( atoi( int_array_content_vector[ i ].c_str() ) );
                                  }
                                  _index_data.insert( make_pair( geometry_id, index_data ) );
                                }
                              }
                            } // L5
                          }
                        } 
                      } // L4
                      _geometry_data.insert( make_pair( geometry_id, triangles_map ) );
                    }
                  }
                } // L3
              }
            }
          } // L2
        } else if( xmlStrcmp( l1->name, ( const xmlChar * )( "library_visual_scenes" ) ) == 0 ){
          xmlNodePtr l2 = NULL;
          for( l2 = l1->children; l2; l2 = l2->next ){
            if( l2->type == XML_ELEMENT_NODE ){
              if( xmlStrcmp( l2->name, ( const xmlChar * )( "visual_scene" ) ) == 0 ){
                xmlNodePtr l3 = NULL;
                for( l3 = l2->children; l3; l3 = l3->next ){
                  if( l3->type == XML_ELEMENT_NODE ){
                    if( xmlStrcmp( l3->name, ( const xmlChar * )( "node" ) ) == 0 ){
                      xmlNodePtr l4 = NULL;
                      for( l4 = l3->children; l4; l4 = l4->next ){
                        if( l4->type == XML_ELEMENT_NODE ){
                          if( xmlStrcmp( l4->name, ( const xmlChar * )( "matrix" ) ) == 0 ){
                            xmlChar* l4_content = xmlNodeGetContent( l4 );
                            string l4_content_string = ( char* )( l4_content );
                            xmlFree( l4_content );
                            vector< string > tv;
                            boost::split( tv, l4_content_string, boost::is_any_of("\n "));
                            for( unsigned int i = 0; i < tv.size(); i++ ){
                              if( tv[ i ] == "" ){
                                tv.erase( tv.begin() + i );
                                i--;
                              }
                            }
                            Frame tmp( Rotation( strtof( tv[0].c_str(), NULL ), strtof( tv[1].c_str(), NULL ), strtof( tv[2].c_str(), NULL ), strtof( tv[4].c_str(), NULL ), strtof( tv[5].c_str(), NULL ), strtof( tv[6].c_str(), NULL ), strtof( tv[8].c_str(), NULL ), strtof( tv[9].c_str(), NULL ), strtof( tv[10].c_str(), NULL ) ), Vector( strtof( tv[3].c_str(), NULL ), strtof( tv[7].c_str(), NULL ), strtof( tv[11].c_str(), NULL ) ) );
                            _dae_offset = _dae_offset * tmp;
                          } else if ( xmlStrcmp( l4->name, ( const xmlChar* )( "node" ) ) == 0 ){
                            xmlNodePtr l5 = NULL;
                            for( l5 = l4->children; l5; l5 = l5->next ){
                              if( l5->type == XML_ELEMENT_NODE ){
                                if( xmlStrcmp( l5->name, ( const xmlChar * )( "matrix" ) ) == 0 ){
                                  xmlChar* l5_content = xmlNodeGetContent( l5 );
                                  string l5_content_string = ( char* )( l5_content );
                                  xmlFree( l5_content );
                                  vector< string > tv;
                                  boost::split( tv, l5_content_string, boost::is_any_of("\n "));
                                  for( unsigned int i = 0; i < tv.size(); i++ ){
                                    if( tv[ i ] == "" ){
                                      tv.erase( tv.begin() + i );
                                      i--;
                                    }
                                  }
                                  Frame tmp( Rotation( strtof( tv[0].c_str(), NULL ), strtof( tv[1].c_str(), NULL ), strtof( tv[2].c_str(), NULL ), strtof( tv[4].c_str(), NULL ), strtof( tv[5].c_str(), NULL ), strtof( tv[6].c_str(), NULL ), strtof( tv[8].c_str(), NULL ), strtof( tv[9].c_str(), NULL ), strtof( tv[10].c_str(), NULL ) ), Vector( strtof( tv[3].c_str(), NULL ), strtof( tv[7].c_str(), NULL ), strtof( tv[11].c_str(), NULL ) ) );
                                  _dae_offset = _dae_offset * tmp;
                                } else if ( xmlStrcmp( l5->name, ( const xmlChar* )( "node" ) ) == 0 ){
                                  xmlNodePtr l6 = NULL;
                                  for( l6 = l5->children; l6; l6 = l6->next ){
                                    if( l6->type == XML_ELEMENT_NODE ){
                                      if( xmlStrcmp( l6->name, ( const xmlChar * )( "matrix" ) ) == 0 ){
                                        xmlChar* l6_content = xmlNodeGetContent( l6 );
                                        string l6_content_string = ( char* )( l6_content );
                                        xmlFree( l6_content );
                                        vector< string > tv;
                                        boost::split( tv, l6_content_string, boost::is_any_of("\n "));
                                        for( unsigned int i = 0; i < tv.size(); i++ ){
                                          if( tv[ i ] == "" ){
                                            tv.erase( tv.begin() + i );
                                            i--;
                                          }
                                        }
                                        Frame tmp( Rotation( strtof( tv[0].c_str(), NULL ), strtof( tv[1].c_str(), NULL ), strtof( tv[2].c_str(), NULL ), strtof( tv[4].c_str(), NULL ), strtof( tv[5].c_str(), NULL ), strtof( tv[6].c_str(), NULL ), strtof( tv[8].c_str(), NULL ), strtof( tv[9].c_str(), NULL ), strtof( tv[10].c_str(), NULL ) ), Vector( strtof( tv[3].c_str(), NULL ), strtof( tv[7].c_str(), NULL ), strtof( tv[11].c_str(), NULL ) ) );
                                        _dae_offset = _dae_offset * tmp;
                                      } else if ( xmlStrcmp( l6->name, ( const xmlChar* )( "node" ) ) == 0 ){
                                        xmlNodePtr l7 = NULL;
                                        for( l7 = l6->children; l7; l7 = l7->next ){
                                          if( l7->type == XML_ELEMENT_NODE ){
                                            if( xmlStrcmp( l7->name, ( const xmlChar * )( "matrix" ) ) == 0 ){
                                              xmlChar* l7_content = xmlNodeGetContent( l7 );
                                              string l7_content_string = ( char* )( l7_content );
                                              xmlFree( l7_content );
                                              vector< string > tv;
                                              boost::split( tv, l7_content_string, boost::is_any_of("\n "));
                                              for( unsigned int i = 0; i < tv.size(); i++ ){
                                                if( tv[ i ] == "" ){
                                                  tv.erase( tv.begin() + i );
                                                  i--;
                                                }
                                              }
                                              Frame tmp( Rotation( strtof( tv[0].c_str(), NULL ), strtof( tv[1].c_str(), NULL ), strtof( tv[2].c_str(), NULL ), strtof( tv[4].c_str(), NULL ), strtof( tv[5].c_str(), NULL ), strtof( tv[6].c_str(), NULL ), strtof( tv[8].c_str(), NULL ), strtof( tv[9].c_str(), NULL ), strtof( tv[10].c_str(), NULL ) ), Vector( strtof( tv[3].c_str(), NULL ), strtof( tv[7].c_str(), NULL ), strtof( tv[11].c_str(), NULL ) ) );
                                              _dae_offset = _dae_offset * tmp;
                                            }
                                          }
                                        }
                                      }
                                    }
                                  } // L6
                                }
                              }
                            } // L5
                          }
                        }
                      } // L4
                    }
                  }
                } // L3
              } 
            }
          } // L2
        }
      }
    } // L1
    xmlFreeDoc( doc );
  } else {
    cout << "could not load file " << filename.c_str() << endl;
  }
  return;
}

map< string, map< string, string > >
OpenGL_Object_DAE::
geometry_data( void )const{
  return _geometry_data;
}

namespace opengl {
  ostream&
  operator<<( ostream& out,
              const OpenGL_Object_DAE& other ) {
    map< string, map< string, string > > geometry_data = other.geometry_data();
    for( map< string, map< string, string > >::const_iterator it1 = geometry_data.begin(); it1 != geometry_data.end(); it1++ ){
      out << "geometry: " << it1->first << endl;
      for( map< string, string >::const_iterator it2 = it1->second.begin(); it2 != it1->second.end(); it2++ ){
        out << "  field: " << it2->first << " name: " << it2->second << endl;
      }
    } 
    return out;
  }
}